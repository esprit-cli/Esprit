"""
Sandbox management service for AWS ECS.

Handles creation, monitoring, and destruction of scan sandboxes.
"""

import asyncio
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import httpx
import structlog
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.models.schemas import SandboxCreateRequest, SandboxCreateResponse, SandboxStatusResponse

logger = structlog.get_logger()
settings = get_settings()

_TOOL_SERVER_CANDIDATE_PORTS = (48081, 5000)
_TOOL_SERVER_TIMEOUT = httpx.Timeout(150.0, connect=5.0)
_TOOL_SERVER_PROBE_TIMEOUT = httpx.Timeout(2.5, connect=1.0)
_TOOL_SERVER_READY_ATTEMPTS = 8
_TOOL_SERVER_READY_RETRY_SECONDS = 1.5


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

    @staticmethod
    def _tool_server_token_for_sandbox(sandbox_id: str) -> str:
        secret = (
            settings.auth_jwt_secret
            or settings.supabase_jwt_secret
            or settings.supabase_service_key
            or "esprit-sandbox"
        )
        digest = hmac.new(
            secret.encode("utf-8"),
            sandbox_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest

    @staticmethod
    def _proxy_tool_server_url(sandbox_id: str) -> str:
        return f"{settings.api_base_url.rstrip('/')}/sandbox/{sandbox_id}"

    def _extract_network_interface_id(self, task: dict[str, Any]) -> str | None:
        attachments = task.get("attachments", [])
        for attachment in attachments:
            if attachment.get("type") != "ElasticNetworkInterface":
                continue
            for detail in attachment.get("details", []):
                if detail.get("name") == "networkInterfaceId":
                    return str(detail.get("value") or "")
        return None

    def _get_task_ips(self, task: dict[str, Any]) -> tuple[str | None, str | None]:
        eni_id = self._extract_network_interface_id(task)
        if not eni_id:
            return None, None

        try:
            eni_response = self.ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
        except ClientError:
            return None, None

        interfaces = eni_response.get("NetworkInterfaces", [])
        if not interfaces:
            return None, None

        first = interfaces[0]
        private_ip = first.get("PrivateIpAddress")
        public_ip = first.get("Association", {}).get("PublicIp")
        return (
            str(private_ip) if private_ip else None,
            str(public_ip) if public_ip else None,
        )

    def _find_owned_sandbox_task(
        self,
        sandbox_id: str,
        user_id: str,
    ) -> tuple[dict[str, Any], str | None, str | None] | None:
        task_arns = self._list_tasks_paginated("RUNNING")
        task_arns.extend(self._list_tasks_paginated("PENDING"))

        if not task_arns:
            return None

        tasks: list[dict[str, Any]] = []
        for index in range(0, len(task_arns), 100):
            chunk = task_arns[index:index + 100]
            tasks_response = self.ecs_client.describe_tasks(
                cluster=settings.ecs_cluster_name,
                tasks=chunk,
                include=["TAGS"],
            )
            tasks.extend(tasks_response.get("tasks", []))

        for task in tasks:
            tags = {t["key"]: t["value"] for t in task.get("tags", [])}
            if tags.get("SandboxId") != sandbox_id or tags.get("UserId") != user_id:
                continue
            private_ip, public_ip = self._get_task_ips(task)
            return task, private_ip, public_ip

        return None

    async def _probe_tool_server_base_url(
        self,
        private_ip: str | None,
        public_ip: str | None,
    ) -> str | None:
        candidate_hosts: list[str] = []
        if private_ip:
            candidate_hosts.append(private_ip)
        if public_ip and public_ip not in candidate_hosts:
            candidate_hosts.append(public_ip)

        if not candidate_hosts:
            return None

        async with httpx.AsyncClient(timeout=_TOOL_SERVER_PROBE_TIMEOUT, trust_env=False) as client:
            for host in candidate_hosts:
                for port in _TOOL_SERVER_CANDIDATE_PORTS:
                    base_url = f"http://{host}:{port}"
                    try:
                        response = await client.get(f"{base_url}/health")
                        if response.status_code != 200:
                            continue
                        payload = response.json()
                        if str(payload.get("status", "")).lower() == "healthy":
                            return base_url
                    except (httpx.HTTPError, ValueError):
                        continue

        return None

    async def _resolve_tool_server_base_url(
        self,
        sandbox_id: str,
        user_id: str,
    ) -> str:
        task_match = self._find_owned_sandbox_task(sandbox_id, user_id)
        if task_match is None:
            raise PermissionError("Sandbox not found or access denied.")

        task, private_ip, public_ip = task_match
        if str(task.get("lastStatus", "")).upper() != "RUNNING":
            raise RuntimeError("Sandbox is not running yet.")

        tool_server_url = await self._probe_tool_server_base_url(private_ip, public_ip)
        if not tool_server_url:
            raise RuntimeError("Tool server is unavailable for this sandbox.")
        return tool_server_url

    async def _resolve_tool_server_base_url_with_retry(
        self,
        sandbox_id: str,
        user_id: str,
    ) -> str:
        last_error: RuntimeError | None = None
        for attempt in range(1, _TOOL_SERVER_READY_ATTEMPTS + 1):
            try:
                return await self._resolve_tool_server_base_url(sandbox_id, user_id)
            except RuntimeError as exc:
                last_error = exc
                if attempt >= _TOOL_SERVER_READY_ATTEMPTS:
                    break
                await asyncio.sleep(_TOOL_SERVER_READY_RETRY_SECONDS)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Tool server is unavailable for this sandbox.")

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
                            # Keep cloud runtime sandboxes alive for interactive tool execution.
                            # The scan-worker path uses launch_scan_task(), not create_sandbox().
                            "command": ["tail", "-f", "/dev/null"],
                            "environment": [
                                {"name": "SCAN_ID", "value": request.scan_id},
                                {"name": "TARGET", "value": request.target},
                                {"name": "TARGET_TYPE", "value": request.target_type},
                                {"name": "SCAN_TYPE", "value": request.scan_type},
                                {"name": "USER_ID", "value": user_id},
                                {"name": "SANDBOX_ID", "value": sandbox_id},
                                {"name": "TOOL_SERVER_TOKEN", "value": self._tool_server_token_for_sandbox(sandbox_id)},
                                # The sandbox will call back to our API for LLM requests
                                {"name": "LLM_PROXY_URL", "value": f"{settings.api_base_url.rstrip('/')}/llm/generate"},
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

    def _list_tasks_paginated(self, desired_status: str) -> list[str]:
        task_arns: list[str] = []
        paginator = self.ecs_client.get_paginator("list_tasks")
        for page in paginator.paginate(
            cluster=settings.ecs_cluster_name,
            desiredStatus=desired_status,
        ):
            task_arns.extend(page.get("taskArns", []))
        return task_arns

    async def get_sandbox_status(self, sandbox_id: str, user_id: str) -> SandboxStatusResponse | None:
        """
        Get the status of a sandbox.
        """
        try:
            task_match = self._find_owned_sandbox_task(sandbox_id, user_id)
            if task_match is None:
                return SandboxStatusResponse(
                    sandbox_id=sandbox_id,
                    status="stopped",
                )

            task, private_ip, public_ip = task_match
            status = "creating"
            tool_server_url: str | None = None
            if str(task.get("lastStatus", "")).upper() == "RUNNING":
                # Only mark the sandbox as running once the tool server is actually reachable.
                resolved = await self._probe_tool_server_base_url(private_ip, public_ip)
                if resolved:
                    status = "running"
                    tool_server_url = self._proxy_tool_server_url(sandbox_id)
            return SandboxStatusResponse(
                sandbox_id=sandbox_id,
                status=status,
                tool_server_url=tool_server_url,
                public_ip=public_ip,
                started_at=task.get("startedAt"),
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
                                {"name": "TOOL_SERVER_TOKEN", "value": self._tool_server_token_for_sandbox(scan_id)},
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

    async def execute_sandbox_tool(
        self,
        sandbox_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Proxy a tool execution request to the sandbox tool server."""
        agent_id = str(payload.get("agent_id") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        kwargs = payload.get("kwargs", {})

        if not agent_id or not tool_name:
            raise ValueError("agent_id and tool_name are required.")
        if not isinstance(kwargs, dict):
            raise ValueError("kwargs must be an object.")

        tool_server_url = await self._resolve_tool_server_base_url_with_retry(sandbox_id, user_id)
        headers = {
            "Authorization": f"Bearer {self._tool_server_token_for_sandbox(sandbox_id)}",
            "Content-Type": "application/json",
        }
        request_body = {"agent_id": agent_id, "tool_name": tool_name, "kwargs": kwargs}

        async with httpx.AsyncClient(timeout=_TOOL_SERVER_TIMEOUT, trust_env=False) as client:
            try:
                response = await client.post(
                    f"{tool_server_url}/execute",
                    json=request_body,
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Tool server returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"Tool server request failed: {exc}") from exc

        return response.json()

    async def fetch_sandbox_diffs(
        self,
        sandbox_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Proxy edit diff retrieval from the sandbox tool server."""
        tool_server_url = await self._resolve_tool_server_base_url_with_retry(sandbox_id, user_id)
        headers = {
            "Authorization": f"Bearer {self._tool_server_token_for_sandbox(sandbox_id)}",
        }

        async with httpx.AsyncClient(timeout=_TOOL_SERVER_TIMEOUT, trust_env=False) as client:
            try:
                response = await client.get(
                    f"{tool_server_url}/diffs",
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Tool server returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"Tool server request failed: {exc}") from exc

        return response.json()

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
                task_arns.update(self._list_tasks_paginated(desired_status))

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

    async def destroy_sandbox(self, sandbox_id: str, user_id: str) -> bool:
        """
        Stop and clean up a sandbox.
        """
        try:
            task_arns: set[str] = set()
            for desired_status in ("RUNNING", "PENDING"):
                task_arns.update(self._list_tasks_paginated(desired_status))

            for task_arn in task_arns:
                tags_response = self.ecs_client.list_tags_for_resource(resourceArn=task_arn)
                tags = {t["key"]: t["value"] for t in tags_response.get("tags", [])}

                if tags.get("SandboxId") == sandbox_id and tags.get("UserId") == user_id:
                    self.ecs_client.stop_task(
                        cluster=settings.ecs_cluster_name,
                        task=task_arn,
                        reason="Sandbox destroyed by user",
                    )
                    logger.info("Sandbox destroyed", sandbox_id=sandbox_id, user_id=user_id)
                    return True

            return False

        except ClientError as e:
            logger.error("Failed to destroy sandbox", error=str(e))
            return False


# Singleton instance
sandbox_service = SandboxService()
