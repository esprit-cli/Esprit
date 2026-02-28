"""
Sandbox management service for AWS ECS.

Handles creation, monitoring, and destruction of scan sandboxes.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3
import structlog
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.models.schemas import SandboxCreateRequest, SandboxCreateResponse, SandboxStatusResponse

logger = structlog.get_logger()
settings = get_settings()


class SandboxService:
    """Service for managing ECS sandbox tasks."""

    def __init__(self) -> None:
        self.ecs_client = boto3.client(
            "ecs",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.ec2_client = boto3.client(
            "ec2",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    async def create_sandbox(
        self,
        request: SandboxCreateRequest,
        user_id: str,
    ) -> SandboxCreateResponse:
        """
        Create a new sandbox (ECS Fargate task) for a scan.
        """
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:12]}"

        try:
            # Run ECS task
            response = self.ecs_client.run_task(
                cluster=settings.ecs_cluster_name,
                taskDefinition=settings.ecs_task_definition,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": settings.ecs_subnets,
                        "securityGroups": settings.ecs_security_groups,
                        "assignPublicIp": "DISABLED",  # Using NAT Gateway for egress
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": "sandbox",
                            "environment": [
                                {"name": "SCAN_ID", "value": request.scan_id},
                                {"name": "TARGET", "value": request.target},
                                {"name": "TARGET_TYPE", "value": request.target_type},
                                {"name": "SCAN_TYPE", "value": request.scan_type},
                                {"name": "USER_ID", "value": user_id},
                                {"name": "SANDBOX_ID", "value": sandbox_id},
                                # The sandbox will call back to our API for LLM requests
                                {"name": "LLM_PROXY_URL", "value": f"{settings.app_name}/api/v1/llm/generate"},
                                # Optional test credentials for authenticated testing
                                {"name": "TEST_USERNAME", "value": request.test_username or ""},
                                {"name": "TEST_PASSWORD", "value": request.test_password or ""},
                            ],
                        }
                    ]
                },
                tags=[
                    {"key": "SandboxId", "value": sandbox_id},
                    {"key": "UserId", "value": user_id},
                    {"key": "ScanId", "value": request.scan_id},
                ],
            )

            task_arn = response["tasks"][0]["taskArn"] if response["tasks"] else None

            logger.info(
                "Sandbox created",
                sandbox_id=sandbox_id,
                task_arn=task_arn,
                user_id=user_id,
            )

            return SandboxCreateResponse(
                sandbox_id=sandbox_id,
                status="creating",
                expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=2),
            )

        except ClientError as e:
            logger.error("Failed to create sandbox", error=str(e))
            raise

    async def get_sandbox_status(self, sandbox_id: str) -> SandboxStatusResponse | None:
        """
        Get the status of a sandbox.
        """
        try:
            # List tasks with the sandbox tag
            response = self.ecs_client.list_tasks(
                cluster=settings.ecs_cluster_name,
                desiredStatus="RUNNING",
            )

            if not response["taskArns"]:
                return SandboxStatusResponse(
                    sandbox_id=sandbox_id,
                    status="stopped",
                )

            # Describe tasks to find our sandbox
            tasks_response = self.ecs_client.describe_tasks(
                cluster=settings.ecs_cluster_name,
                tasks=response["taskArns"],
            )

            for task in tasks_response["tasks"]:
                # Check tags for matching sandbox ID
                tags = {t["key"]: t["value"] for t in task.get("tags", [])}
                if tags.get("SandboxId") == sandbox_id:
                    # Get public IP
                    public_ip = None
                    attachments = task.get("attachments", [])
                    for attachment in attachments:
                        if attachment["type"] == "ElasticNetworkInterface":
                            for detail in attachment.get("details", []):
                                if detail["name"] == "networkInterfaceId":
                                    eni_id = detail["value"]
                                    # Get ENI details for public IP
                                    eni_response = self.ec2_client.describe_network_interfaces(
                                        NetworkInterfaceIds=[eni_id]
                                    )
                                    if eni_response["NetworkInterfaces"]:
                                        association = eni_response["NetworkInterfaces"][0].get("Association", {})
                                        public_ip = association.get("PublicIp")

                    status = "running" if task["lastStatus"] == "RUNNING" else "creating"
                    tool_server_url = f"http://{public_ip}:5000" if public_ip else None

                    return SandboxStatusResponse(
                        sandbox_id=sandbox_id,
                        status=status,
                        tool_server_url=tool_server_url,
                        public_ip=public_ip,
                        started_at=task.get("startedAt"),
                    )

            return SandboxStatusResponse(
                sandbox_id=sandbox_id,
                status="stopped",
            )

        except ClientError as e:
            logger.error("Failed to get sandbox status", error=str(e))
            return None

    async def launch_scan_task(
        self,
        scan_id: str,
        target_value: str,
        user_id: str,
        target_type: str = "repository",
        github_token: str | None = None,
        scan_type: str = "standard",
        max_iterations: int = 50,
        max_duration_seconds: int = 1800,
        llm_timeout_seconds: int = 300,
        budget_usd: float = 2.00,
        test_username: str | None = None,
        test_password: str | None = None,
    ) -> str | None:
        """
        Launch an ECS Fargate task to run a security scan.

        Args:
            scan_id: UUID of the scan record
            target_value: Target to scan (URL, domain, IP, or repo URL)
            user_id: User ID who initiated the scan
            target_type: Type of target ("repository" or "url")
            github_token: GitHub App installation access token (for repositories)
            scan_type: Scan tier (quick/standard/deep)
            max_iterations: Maximum agent iterations for this scan
            max_duration_seconds: Maximum scan duration in seconds
            llm_timeout_seconds: Timeout for each LLM call
            budget_usd: Maximum budget in USD for this scan
            test_username: Optional username for authenticated testing
            test_password: Optional password for authenticated testing

        Returns:
            Task ARN if successful, None otherwise
        """
        try:
            # Use the sandbox task definition for scanning
            # Container name must match what's in Terraform task definition
            container_name = "sandbox"
            patchable_target_types = {"repository", "public_repository", "local_upload"}
            patch_s3_key = (
                f"patches/{user_id}/{scan_id}.patch" if target_type in patchable_target_types else ""
            )

            response = self.ecs_client.run_task(
                cluster=settings.ecs_cluster_name,
                taskDefinition="esprit-prod-sandbox",  # Sandbox task definition
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": settings.ecs_subnets,
                        "securityGroups": settings.ecs_security_groups,
                        "assignPublicIp": "DISABLED",  # Using NAT Gateway for egress
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": container_name,
                            "environment": [
                                # Scan identification
                                {"name": "SCAN_ID", "value": scan_id},
                                {"name": "USER_ID", "value": user_id},
                                # Target configuration
                                {"name": "TARGET_TYPE", "value": target_type},
                                {"name": "TARGET_VALUE", "value": target_value},
                                # Scan tier configuration
                                {"name": "SCAN_TYPE", "value": scan_type},
                                {"name": "MAX_ITERATIONS", "value": str(max_iterations)},
                                {"name": "MAX_SCAN_DURATION", "value": str(max_duration_seconds)},
                                {"name": "LLM_TIMEOUT", "value": str(llm_timeout_seconds)},
                                # Budget limit (primary constraint)
                                {"name": "SCAN_BUDGET_USD", "value": str(budget_usd)},
                                # GitHub credentials (for repository targets)
                                {"name": "GITHUB_REPO_URL", "value": target_value if target_type == "repository" else ""},
                                {"name": "GITHUB_TOKEN", "value": github_token or ""},
                                # Supabase for log streaming
                                {"name": "SUPABASE_URL", "value": settings.supabase_url},
                                {"name": "SUPABASE_SERVICE_KEY", "value": settings.supabase_service_key},
                                # AWS Bedrock credentials (ESPRIT_LLM is set by entrypoint.sh default)
                                {"name": "AWS_DEFAULT_REGION", "value": settings.aws_region},
                                {"name": "AWS_ACCESS_KEY_ID", "value": settings.aws_access_key_id or ""},
                                {"name": "AWS_SECRET_ACCESS_KEY", "value": settings.aws_secret_access_key or ""},
                                # Optional test credentials for authenticated testing
                                {"name": "TEST_USERNAME", "value": test_username or ""},
                                {"name": "TEST_PASSWORD", "value": test_password or ""},
                                # S3 configuration for local_upload targets (paths include user_id for isolation)
                                {"name": "S3_BUCKET", "value": settings.s3_bucket or ""},
                                {"name": "UPLOAD_S3_KEY", "value": f"uploads/{user_id}/{scan_id}.tar.gz" if target_type == "local_upload" else ""},
                                {"name": "PATCH_S3_KEY", "value": patch_s3_key},
                            ],
                        }
                    ]
                },
                tags=[
                    {"key": "ScanId", "value": scan_id},
                    {"key": "UserId", "value": user_id},
                    {"key": "Type", "value": "scan"},
                    {"key": "ScanType", "value": scan_type},
                ],
            )

            task_arn = response["tasks"][0]["taskArn"] if response["tasks"] else None

            if not task_arn and response.get("failures"):
                failure = response["failures"][0]
                logger.error(
                    "ECS task launch failed",
                    scan_id=scan_id,
                    reason=failure.get("reason"),
                    detail=failure.get("detail"),
                )
                raise RuntimeError(f"ECS task failed: {failure.get('reason')}")

            logger.info(
                "Scan task launched",
                scan_id=scan_id,
                task_arn=task_arn,
                user_id=user_id,
                target=target_value,
            )

            return task_arn

        except ClientError as e:
            logger.error(
                "Failed to launch scan task",
                scan_id=scan_id,
                error=str(e),
            )
            raise

    async def stop_task(self, task_arn: str) -> bool:
        """
        Stop a specific ECS task by ARN.
        """
        try:
            self.ecs_client.stop_task(
                cluster=settings.ecs_cluster_name,
                task=task_arn,
                reason="Scan cancelled by user",
            )
            logger.info("Task stopped", task_arn=task_arn)
            return True
        except ClientError as e:
            logger.error("Failed to stop task", task_arn=task_arn, error=str(e))
            return False

    async def stop_tasks_for_scan(self, scan_id: str) -> int:
        """Stop all ECS tasks tagged with the given scan ID."""
        stopped = 0
        try:
            task_arns: set[str] = set()
            for desired_status in ("RUNNING", "PENDING"):
                response = self.ecs_client.list_tasks(
                    cluster=settings.ecs_cluster_name,
                    desiredStatus=desired_status,
                )
                task_arns.update(response.get("taskArns", []))

            for task_arn in task_arns:
                tags_response = self.ecs_client.list_tags_for_resource(resourceArn=task_arn)
                tags = {t["key"]: t["value"] for t in tags_response.get("tags", [])}
                if tags.get("ScanId") != scan_id:
                    continue
                self.ecs_client.stop_task(
                    cluster=settings.ecs_cluster_name,
                    task=task_arn,
                    reason="Scan cancelled/deleted by user",
                )
                stopped += 1

            if stopped:
                logger.info("Stopped scan tasks by ScanId", scan_id=scan_id, stopped=stopped)
        except ClientError as e:
            logger.error("Failed to stop tasks by scan id", scan_id=scan_id, error=str(e))
        return stopped

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """
        Stop and clean up a sandbox.
        """
        try:
            # Find and stop the task
            response = self.ecs_client.list_tasks(
                cluster=settings.ecs_cluster_name,
                desiredStatus="RUNNING",
            )

            for task_arn in response.get("taskArns", []):
                # Check if this is our sandbox
                tags_response = self.ecs_client.list_tags_for_resource(resourceArn=task_arn)
                tags = {t["key"]: t["value"] for t in tags_response.get("tags", [])}

                if tags.get("SandboxId") == sandbox_id:
                    self.ecs_client.stop_task(
                        cluster=settings.ecs_cluster_name,
                        task=task_arn,
                        reason="Sandbox destroyed by user",
                    )
                    logger.info("Sandbox destroyed", sandbox_id=sandbox_id)
                    return True

            return False

        except ClientError as e:
            logger.error("Failed to destroy sandbox", error=str(e))
            return False


# Singleton instance
sandbox_service = SandboxService()
