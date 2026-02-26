"""
API routes for the Esprit Backend service.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json
from typing import Any, Dict, List
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from supabase import create_client

from app.core.auth import CurrentUser
from app.core.config import settings, get_scan_tier_config
from app.models.schemas import (
    LLMGenerateRequest,
    LLMGenerateResponse,
    QuotaCheckResponse,
    SandboxCreateRequest,
    SandboxCreateResponse,
    SandboxStatusResponse,
    ScanCreateRequest,
    ScanCreateResponse,
    ScanStatusResponse,
    ScanLogEntry,
    ScanLogsResponse,
    SubscriptionVerifyResponse,
    UsageResponse,
)
from app.services.llm_service import LLMServiceError, llm_service
from app.services.sandbox_service import sandbox_service
from app.services.usage_service import usage_service

router = APIRouter()

# Supabase client for database operations
supabase = create_client(settings.supabase_url, settings.supabase_service_key)


# Simple in-memory rate limiter for presigned URL generation
class PresignedUrlRateLimiter:
    """
    In-memory rate limiter to prevent abuse of presigned URL generation.

    Limits: 5 presigned URLs per user per hour.
    This prevents users from requesting many URLs and uploading large data without starting scans.

    Note: This is an in-memory rate limiter and does NOT persist across:
    - Multiple worker processes (if using multi-worker deployment)
    - Server restarts
    For production with multiple workers, consider using Redis or database-backed rate limiting.
    Single-worker deployment (current setup) works correctly with this approach.
    """

    def __init__(self, max_requests: int = 5, window_hours: int = 1):
        self.max_requests = max_requests
        self.window_hours = window_hours
        # Store timestamps of requests per user_id
        self._requests: Dict[str, List[datetime]] = defaultdict(list)
        # Lock for thread-safe access (asyncio-compatible)
        import asyncio
        self._lock = asyncio.Lock()

    async def check_and_record(self, user_id: str) -> tuple[bool, int | None, str | None]:
        """
        Check if user is within rate limit and record the request.

        Returns:
            (is_allowed, retry_after_seconds, error_message)
        """
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            cutoff = now - timedelta(hours=self.window_hours)

            # Clean up old requests
            self._requests[user_id] = [
                ts for ts in self._requests[user_id]
                if ts > cutoff
            ]

            # Check rate limit
            if len(self._requests[user_id]) >= self.max_requests:
                oldest_request = min(self._requests[user_id])
                time_until_reset = oldest_request + timedelta(hours=self.window_hours) - now
                seconds_remaining = int(time_until_reset.total_seconds())
                minutes_remaining = seconds_remaining // 60

                return False, seconds_remaining, f"Rate limit exceeded. You can request {self.max_requests} presigned URLs per hour. Please try again in {minutes_remaining} minutes."

            # Record this request
            self._requests[user_id].append(now)
            return True, None, None

    async def cleanup_old_entries(self):
        """Periodically clean up users who haven't made requests recently."""
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            cutoff = now - timedelta(hours=self.window_hours * 2)

            users_to_remove = [
                user_id for user_id, timestamps in self._requests.items()
                if not timestamps or max(timestamps) < cutoff
            ]

            for user_id in users_to_remove:
                del self._requests[user_id]


# Global rate limiter instance
presigned_url_limiter = PresignedUrlRateLimiter(max_requests=5, window_hours=1)

FREE_ALLOWED_MODEL_ALIASES = {
    "default",
    "haiku",
    "haiku-4.5",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


class UserRequestRateLimiter:
    """Simple in-memory per-user limiter for request burst control."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[datetime]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check_and_record(self, user_id: str) -> tuple[bool, int | None]:
        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            cutoff = now - timedelta(seconds=self.window_seconds)
            self._requests[user_id] = [ts for ts in self._requests[user_id] if ts > cutoff]

            if len(self._requests[user_id]) >= self.max_requests:
                oldest_request = min(self._requests[user_id])
                reset_after = oldest_request + timedelta(seconds=self.window_seconds) - now
                return False, max(1, int(reset_after.total_seconds()))

            self._requests[user_id].append(now)
            return True, None


llm_request_limiter = UserRequestRateLimiter(
    max_requests=max(1, settings.llm_requests_per_minute),
    window_seconds=60,
)
_llm_quota_locks: Dict[str, asyncio.Lock] = {}
_llm_quota_locks_guard = asyncio.Lock()


async def _get_llm_quota_lock(user_id: str) -> asyncio.Lock:
    async with _llm_quota_locks_guard:
        lock = _llm_quota_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _llm_quota_locks[user_id] = lock
        return lock


def _is_free_model_allowed(model_name: str | None) -> bool:
    if not model_name:
        return True
    normalized = model_name.strip().lower()
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]
    return normalized in FREE_ALLOWED_MODEL_ALIASES


def _enforce_free_model_allowlist(payload: LLMGenerateRequest, model_hint: str | None) -> str:
    """Force free users onto safe aliases regardless of client hints."""
    if not _is_free_model_allowed(payload.model):
        payload.model = "default"
    if not _is_free_model_allowed(model_hint):
        return "default"
    return model_hint or "default"


async def _execute_llm_request(
    payload: LLMGenerateRequest,
    user: CurrentUser,
    raw_request: Request,
) -> LLMGenerateResponse:
    plan = await usage_service.get_user_plan(user.sub)

    allowed, retry_after = await llm_request_limiter.check_and_record(user.sub)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="LLM request rate limit exceeded. Please retry shortly.",
            headers={"Retry-After": str(retry_after)} if retry_after is not None else None,
        )

    provider_hint = raw_request.headers.get("X-Esprit-Provider")
    model_hint = raw_request.headers.get("X-Esprit-Model")
    if plan == "free":
        claim = await usage_service.get_free_scan_claim(user.sub)
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="No free scan claim found. Start your free scan first.",
            )
        model_hint = _enforce_free_model_allowlist(payload, model_hint)

    enforce_scan_limit = plan != "free"
    llm_quota_lock = await _get_llm_quota_lock(user.sub)
    async with llm_quota_lock:
        quota = await usage_service.check_quota(user.sub, enforce_scan_limit=enforce_scan_limit)
        if not quota.has_quota:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=quota.message or "Scan quota exceeded. Upgrade your plan for more scans.",
            )

        try:
            result = await llm_service.generate(
                payload,
                user.sub,
                provider_hint=provider_hint,
                model_hint=model_hint,
            )
        except LLMServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        await usage_service.add_tokens_used(user.sub, result.tokens_used)
        return result


# Public stats models (no auth required)
class PublicStatsResponse(BaseModel):
    total_vulnerabilities_found: int


# GitHub OAuth models
class GitHubCallbackRequest(BaseModel):
    code: str


class GitHubCallbackResponse(BaseModel):
    access_token: str
    username: str


# Health check
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@router.get("/public/stats", response_model=PublicStatsResponse)
async def public_stats():
    """
    Public, non-authenticated stats for the marketing site.

    Uses the Supabase service role key server-side to aggregate across all users.
    """
    try:
        # Count all vulnerabilities ever recorded.
        # Note: count comes from PostgREST headers (Prefer: count=exact).
        resp = supabase.table("vulnerabilities").select("id", count="exact").execute()
        total = int(resp.count or 0)
        return PublicStatsResponse(total_vulnerabilities_found=total)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load public stats: {str(e)}",
        )


# Sandbox endpoints
@router.post("/sandbox", response_model=SandboxCreateResponse)
async def create_sandbox(
    request: SandboxCreateRequest,
    user: CurrentUser,
):
    """
    Create a new sandbox for a penetration test scan.

    This spins up an ECS Fargate task with the Esprit sandbox container.
    """
    # Check quota first (with optional bypass code)
    quota = await usage_service.check_quota(user.sub, request.bypass_code)
    if not quota.has_quota:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=quota.message or "Quota exceeded. Upgrade your plan at /billing",
        )

    # Create sandbox
    result = await sandbox_service.create_sandbox(request, user.sub)

    # Increment scan count
    await usage_service.increment_scan_count(user.sub)

    return result


@router.get("/sandbox/{sandbox_id}", response_model=SandboxStatusResponse)
async def get_sandbox_status(
    sandbox_id: str,
    user: CurrentUser,
):
    """Get the status of a sandbox."""
    result = await sandbox_service.get_sandbox_status(sandbox_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sandbox not found",
        )
    return result


@router.delete("/sandbox/{sandbox_id}")
async def destroy_sandbox(
    sandbox_id: str,
    user: CurrentUser,
):
    """Stop and clean up a sandbox."""
    success = await sandbox_service.destroy_sandbox(sandbox_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sandbox not found or already stopped",
        )
    return {"status": "destroyed"}


# LLM Proxy endpoints
@router.post("/llm/generate", response_model=LLMGenerateResponse)
async def generate_llm_response(
    payload: LLMGenerateRequest,
    user: CurrentUser,
    raw_request: Request,
):
    """
    Proxy LLM request through Esprit's Bedrock-backed cloud models.

    This allows users to run scans without needing their own API keys.
    """
    return await _execute_llm_request(payload, user, raw_request)


def _normalize_openai_compat_payload(payload: dict[str, Any]) -> LLMGenerateRequest:
    model = payload.get("model")
    if isinstance(model, str) and "/" in model:
        model = model.split("/", 1)[1]

    max_tokens = payload.get("max_tokens", payload.get("max_completion_tokens", 4096))
    try:
        max_tokens_int = int(max_tokens)
    except (TypeError, ValueError):
        max_tokens_int = 4096

    temperature = payload.get("temperature", 0.7)
    try:
        temperature_float = float(temperature)
    except (TypeError, ValueError):
        temperature_float = 0.7

    tools = payload.get("tools")
    if not isinstance(tools, list):
        tools = None

    reasoning_effort = payload.get("reasoning_effort")
    if not isinstance(reasoning_effort, str):
        reasoning_effort = None

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Messages cannot be empty.",
        )

    return LLMGenerateRequest(
        messages=messages,
        model=model if isinstance(model, str) else None,
        temperature=temperature_float,
        max_tokens=max_tokens_int,
        scan_id=payload.get("scan_id") if isinstance(payload.get("scan_id"), str) else None,
        tools=tools,
        reasoning_effort=reasoning_effort,
    )


def _format_openai_compat_response(result: LLMGenerateResponse) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": result.content,
    }
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls

    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(datetime.now(tz=timezone.utc).timestamp()),
        "model": result.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": result.tokens_used,
            "total_tokens": result.tokens_used,
        },
    }


@router.post("/llm/generate/chat/completions")
async def generate_llm_chat_completions_compat(
    payload: dict[str, Any],
    user: CurrentUser,
    raw_request: Request,
):
    """
    Backward-compatible OpenAI chat-completions surface for older CLIs.

    Older Esprit binaries use LiteLLM OpenAI routing against the cloud proxy base URL,
    which appends /chat/completions. This shim maps that shape to /llm/generate.
    """
    llm_payload = _normalize_openai_compat_payload(payload)
    stream_requested = bool(payload.get("stream"))

    result = await _execute_llm_request(llm_payload, user, raw_request)
    completion = _format_openai_compat_response(result)

    if not stream_requested:
        return completion

    initial_delta: dict[str, Any] = {
        "role": "assistant",
        "content": completion["choices"][0]["message"].get("content", ""),
    }
    tool_calls = completion["choices"][0]["message"].get("tool_calls")
    if isinstance(tool_calls, list):
        initial_delta["tool_calls"] = tool_calls

    first_chunk = {
        "id": completion["id"],
        "object": "chat.completion.chunk",
        "created": completion["created"],
        "model": completion["model"],
        "choices": [
            {
                "index": 0,
                "delta": initial_delta,
                "finish_reason": None,
            }
        ],
    }
    final_chunk = {
        "id": completion["id"],
        "object": "chat.completion.chunk",
        "created": completion["created"],
        "model": completion["model"],
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": completion["choices"][0]["finish_reason"],
            }
        ],
    }

    async def _stream() -> Any:
        yield f"data: {json.dumps(first_chunk)}\n\n"
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# Usage endpoints
@router.get("/user/usage", response_model=UsageResponse)
async def get_user_usage(user: CurrentUser):
    """Get current user's usage stats for this month."""
    return await usage_service.get_usage(user.sub)


@router.get("/user/quota", response_model=QuotaCheckResponse)
async def check_user_quota(user: CurrentUser):
    """Check if user has remaining quota for a new scan."""
    return await usage_service.check_quota(user.sub)


@router.get("/subscription/verify", response_model=SubscriptionVerifyResponse)
async def verify_subscription(user: CurrentUser):
    """Return subscription validity, quotas, and cloud model access for CLI."""
    try:
        plan = await usage_service.get_user_plan(user.sub)
        quota = await usage_service.check_quota(user.sub)
        scans_remaining = max(0, int(quota.scans_remaining))
        tokens_remaining = max(0, int(quota.tokens_remaining))

        if plan == "free":
            cloud_enabled = scans_remaining > 0
            available_models = usage_service.free_available_models() if cloud_enabled else []
        else:
            cloud_enabled = bool(quota.has_quota)
            available_models = ["default", "haiku", "kimi-k2.5"] if cloud_enabled else []

        return SubscriptionVerifyResponse(
            valid=True,
            plan=plan,
            quota_remaining={"scans": scans_remaining, "tokens": tokens_remaining},
            cloud_enabled=cloud_enabled,
            available_models=available_models,
            error=None,
        )
    except Exception:  # noqa: BLE001
        # Fail closed when verification cannot complete.
        return SubscriptionVerifyResponse(
            valid=False,
            plan="free",
            quota_remaining={"scans": 0, "tokens": 0},
            cloud_enabled=False,
            available_models=[],
            error="verification_unavailable",
        )


# GitHub OAuth endpoints
@router.post("/github/callback", response_model=GitHubCallbackResponse)
async def github_callback(
    request: GitHubCallbackRequest,
    user: CurrentUser,
):
    """
    Exchange GitHub OAuth code for access token.

    This is called from the frontend after GitHub redirects back.
    """
    try:
        async with httpx.AsyncClient() as client:
            # Exchange code for token
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "code": request.code,
                },
                headers={"Accept": "application/json"},
            )

            if token_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange code for token",
                )

            token_data = token_response.json()
            access_token = token_data.get("access_token")

            if not access_token:
                error = token_data.get("error_description", "Unknown error")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"GitHub OAuth error: {error}",
                )

            # Get user info
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            if user_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to fetch GitHub user info",
                )

            github_user = user_response.json()

            return GitHubCallbackResponse(
                access_token=access_token,
                username=github_user.get("login", ""),
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"GitHub API request failed: {str(e)}",
        )


# GitHub App models
class GitHubAppInstallationRequest(BaseModel):
    installation_id: int
    setup_action: str | None = None  # "install" or "update"


class GitHubAppStatusResponse(BaseModel):
    installed: bool
    installation_id: int | None = None
    installed_at: str | None = None


@router.post("/github/app/installation")
async def save_github_app_installation(
    request: GitHubAppInstallationRequest,
    user: CurrentUser,
):
    """
    Save GitHub App installation ID for a user.

    Called after user installs the GitHub App on their account.
    """
    import structlog
    logger = structlog.get_logger()

    logger.info("Saving GitHub App installation", user_id=user.sub, installation_id=request.installation_id)

    try:
        # First check if profile exists
        profile_check = supabase.table("profiles").select("id").eq("id", user.sub).execute()

        if not profile_check.data:
            # Profile doesn't exist, create it
            logger.info("Profile doesn't exist, creating", user_id=user.sub)
            supabase.table("profiles").insert({
                "id": user.sub,
                "github_app_installation_id": request.installation_id,
                "github_app_installed_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        else:
            # Update existing profile
            response = supabase.table("profiles").update({
                "github_app_installation_id": request.installation_id,
                "github_app_installed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", user.sub).execute()

            logger.info("Profile updated", user_id=user.sub, response_data=response.data)

        return {"success": True, "installation_id": request.installation_id}

    except Exception as e:
        logger.error("Failed to save installation", error=str(e), user_id=user.sub)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save installation: {str(e)}",
        )


@router.get("/github/app/status", response_model=GitHubAppStatusResponse)
async def get_github_app_status(user: CurrentUser):
    """Check if user has installed the GitHub App."""
    response = supabase.table("profiles").select(
        "github_app_installation_id, github_app_installed_at"
    ).eq("id", user.sub).single().execute()

    if not response.data:
        return GitHubAppStatusResponse(installed=False)

    installation_id = response.data.get("github_app_installation_id")
    return GitHubAppStatusResponse(
        installed=installation_id is not None,
        installation_id=installation_id,
        installed_at=response.data.get("github_app_installed_at"),
    )


@router.delete("/github/app/installation")
async def remove_github_app_installation(user: CurrentUser):
    """Remove GitHub App installation from user profile."""
    supabase.table("profiles").update({
        "github_app_installation_id": None,
        "github_app_installed_at": None,
    }).eq("id", user.sub).execute()

    # Also clear linked repos
    supabase.table("linked_repos").delete().eq("user_id", user.sub).execute()

    return {"success": True}


@router.post("/github/webhook")
async def github_webhook(request: dict):
    """
    Handle GitHub App webhook events.

    Events we may handle in the future:
    - installation: App installed/uninstalled
    - push: Code pushed to repo
    - pull_request: PR opened/updated
    """
    action = request.get("action")
    event_type = request.get("installation", {}).get("id") if "installation" in request else None

    # Log the webhook for debugging
    import structlog
    logger = structlog.get_logger()
    logger.info("GitHub webhook received", action=action, installation_id=event_type)

    # For now, just acknowledge receipt
    return {"status": "ok"}


# GitHub Repos models
class GitHubRepoInfo(BaseModel):
    id: int
    name: str
    full_name: str
    owner: str
    html_url: str
    default_branch: str
    private: bool
    description: str | None = None


class GitHubReposResponse(BaseModel):
    repos: list[GitHubRepoInfo]
    total: int


async def get_github_app_installation_token(installation_id: int) -> str:
    """
    Generate an installation access token for a GitHub App installation.

    This requires:
    1. Create a JWT signed with the App's private key
    2. Exchange it for an installation access token
    """
    import time
    from jose import jwt

    if not settings.github_app_id or not settings.github_app_private_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub App not configured",
        )

    # Create JWT for GitHub App authentication
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60 seconds ago (clock skew)
        "exp": now + 600,  # Expires in 10 minutes
        "iss": settings.github_app_id,
    }

    # The private key needs newlines restored if stored as single line
    private_key = settings.github_app_private_key.replace("\\n", "\n")

    app_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    # Exchange JWT for installation token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        if response.status_code != 201:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to get installation token: {response.text}",
            )

        return response.json()["token"]


# Scan models
class ScanStartRequest(BaseModel):
    """Optional request body for scan start."""
    instruction: str | None = None
    bypass_code: str | None = None  # Optional code to bypass quota limits


class ScanStartResponse(BaseModel):
    scan_id: str
    status: str
    task_arn: str | None = None
    message: str


import uuid


@router.post("/scans", response_model=ScanCreateResponse)
async def create_scan(
    request: ScanCreateRequest,
    user: CurrentUser,
):
    """
    Create a new scan record.

    This endpoint creates the scan in Supabase so the CLI doesn't need
    to call Supabase directly (avoiding JWT compatibility issues).
    """
    scan_id = str(uuid.uuid4())

    scan_data = {
        "id": scan_id,
        "user_id": user.sub,
        "target": request.target,
        "target_type": request.target_type,
        "scan_type": request.scan_type,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Note: instruction is passed to sandbox at start time, not stored in DB

    try:
        supabase.table("scans").insert(scan_data).execute()
        return ScanCreateResponse(scan_id=scan_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create scan: {str(e)}",
        )


@router.get("/scans/{scan_id}", response_model=ScanStatusResponse)
async def get_scan_status(
    scan_id: str,
    user: CurrentUser,
):
    """
    Get scan status and details.

    Returns the current status, vulnerability counts, and timestamps.
    """
    response = supabase.table("scans").select("*").eq("id", scan_id).execute()

    if not response.data or len(response.data) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = response.data[0]

    # Verify scan belongs to user
    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this scan",
        )

    return ScanStatusResponse(
        scan_id=scan["id"],
        status=scan.get("status", "unknown"),
        target=scan.get("target", ""),
        target_type=scan.get("target_type", ""),
        scan_type=scan.get("scan_type", "standard"),
        vulnerabilities_found=scan.get("vulnerabilities_found", 0),
        critical_count=scan.get("critical_count", 0),
        high_count=scan.get("high_count", 0),
        medium_count=scan.get("medium_count", 0),
        low_count=scan.get("low_count", 0),
        created_at=scan.get("created_at"),
        started_at=scan.get("started_at"),
        completed_at=scan.get("completed_at"),
    )


@router.get("/scans/{scan_id}/logs", response_model=ScanLogsResponse)
async def get_scan_logs(
    scan_id: str,
    user: CurrentUser,
    after_id: str | None = None,
):
    """
    Get scan logs.

    Supports pagination via after_id parameter for streaming logs.
    """
    # First verify scan belongs to user
    scan_response = supabase.table("scans").select("user_id").eq("id", scan_id).execute()

    if not scan_response.data or len(scan_response.data) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    if scan_response.data[0].get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this scan",
        )

    # Get logs
    query = supabase.table("scan_logs").select("*").eq("scan_id", scan_id).order("created_at", desc=False)

    if after_id:
        # Get logs created after the specified log
        after_log = supabase.table("scan_logs").select("created_at").eq("id", after_id).execute()
        if after_log.data and len(after_log.data) > 0:
            query = query.gt("created_at", after_log.data[0]["created_at"])

    response = query.limit(100).execute()

    logs = [
        ScanLogEntry(
            id=log["id"],
            created_at=log["created_at"],
            log_type=log.get("log_type", "info"),
            content=log.get("content", ""),
        )
        for log in (response.data or [])
    ]

    return ScanLogsResponse(scan_id=scan_id, logs=logs)


@router.post("/scans/{scan_id}/start", response_model=ScanStartResponse)
async def start_scan(
    scan_id: str,
    user: CurrentUser,
    request: ScanStartRequest | None = None,
):
    """
    Start a scan by launching an ECS sandbox task.

    This endpoint:
    1. Fetches scan details from Supabase
    2. Gets GitHub App installation token for the user
    3. Launches ECS Fargate task with repo credentials
    4. Updates scan status to 'running'
    """
    # Fetch scan record
    scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

    if not scan_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = scan_response.data

    # Verify scan belongs to user
    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to start this scan",
        )

    # Check scan status
    if scan.get("status") not in ["pending", "failed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scan cannot be started (status: {scan.get('status')})",
        )

    user_plan = await usage_service.get_user_plan(user.sub)
    scan_type = scan.get("scan_type", "standard")
    if user_plan == "free" and scan_type != "quick":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Free plan supports quick cloud scan mode only.",
        )

    # Check quota first (with optional bypass code)
    bypass_code = request.bypass_code if request else None
    quota = await usage_service.check_quota(user.sub, bypass_code)
    if not quota.has_quota:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=quota.message or "Quota exceeded. Upgrade your plan at /billing",
        )

    if user_plan == "free":
        claimed, claim_error = await usage_service.claim_free_scan(user.sub, scan_id)
        if not claimed:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=claim_error or "Your free scan has already been used.",
            )

    # Get target and determine target type
    target = scan.get("target", "")
    target_type = scan.get("target_type", "repository")

    # Detect local uploads: target_type is "repository" but target doesn't look like a GitHub URL
    # Local uploads have targets like "mangafusion" (folder name), not "github.com/owner/repo"
    is_local_upload = (
        target_type == "repository" and
        "/" not in target and
        "github.com" not in target.lower()
    )
    if is_local_upload:
        target_type = "local_upload"

    # For URL targets, we don't need GitHub credentials
    github_token = None

    if target_type == "repository":
        # Private repository via GitHub App - requires authentication
        profile_response = supabase.table("profiles").select(
            "github_app_installation_id"
        ).eq("id", user.sub).single().execute()

        if not profile_response.data or not profile_response.data.get("github_app_installation_id"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub App not installed. Please connect your GitHub account first.",
            )

        installation_id = profile_response.data["github_app_installation_id"]
        github_token = await get_github_app_installation_token(installation_id)

        # Normalize repo URL (remove https:// prefix if present)
        if target.startswith("https://"):
            target = target.replace("https://", "")

    elif target_type == "public_repository":
        # Public repository from GitHub URL - no authentication needed
        # Will be cloned without token (works for public repos)
        github_token = None

        # Normalize repo URL (remove https:// prefix if present)
        if target.startswith("https://"):
            target = target.replace("https://", "")

    try:
        # Update scan status to running
        supabase.table("scans").update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", scan_id).execute()

        # Get scan tier configuration
        scan_type = scan.get("scan_type", "standard")
        tier_config = get_scan_tier_config(scan_type)

        # Get test credentials if provided
        test_username = scan.get("test_username")
        test_password = scan.get("test_password")

        # Launch ECS sandbox task with tier-specific limits (budget is primary constraint)
        task_arn = await sandbox_service.launch_scan_task(
            scan_id=scan_id,
            target_value=target,
            user_id=user.sub,
            target_type=target_type,
            github_token=github_token,
            scan_type=scan_type,
            max_iterations=tier_config['max_iterations'],
            max_duration_seconds=tier_config['max_duration_seconds'],
            llm_timeout_seconds=tier_config['llm_timeout_seconds'],
            budget_usd=tier_config['budget_usd'],
            test_username=test_username,
            test_password=test_password,
        )

        # Save task_arn to scan record so we can stop it later
        if task_arn:
            supabase.table("scans").update({
                "task_arn": task_arn,
            }).eq("id", scan_id).execute()

        # Increment scan count
        await usage_service.increment_scan_count(user.sub)

        return ScanStartResponse(
            scan_id=scan_id,
            status="running",
            task_arn=task_arn,
            message="Scan started successfully",
        )

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Update scan status to failed
        supabase.table("scans").update({
            "status": "failed",
        }).eq("id", scan_id).execute()

        import structlog
        logger = structlog.get_logger()
        logger.error("Failed to start scan", scan_id=scan_id, error=str(e))

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start scan: {str(e)}",
        )


# ============================================================================
# Local Folder Upload Support (S3 Presigned URLs)
# ============================================================================

class UploadUrlRequest(BaseModel):
    """Request for getting a presigned upload URL."""
    scan_id: str


class UploadUrlResponse(BaseModel):
    """Response with presigned URL for folder upload."""
    upload_url: str
    s3_key: str


class PatchUrlResponse(BaseModel):
    """Response with presigned URL for patch download."""
    download_url: str | None
    has_patch: bool


@router.post("/uploads/presigned-url", response_model=UploadUrlResponse)
async def get_upload_url(
    request: UploadUrlRequest,
    user: CurrentUser,
):
    """
    Generate a presigned S3 URL for uploading a local folder (as tar.gz).

    The CLI uses this to upload local folders for cloud scanning.
    Security: Validates UUID format, checks user quota, rate limits requests, and isolates uploads by user_id.
    """
    import uuid

    import boto3
    from botocore.config import Config

    # Rate limit check FIRST to prevent abuse
    is_allowed, retry_after, error_message = await presigned_url_limiter.check_and_record(user.sub)
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_message,
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )

    # Validate scan_id is a valid UUID format
    try:
        uuid.UUID(request.scan_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid scan_id format",
        )

    # Check if scan already exists - if so, verify ownership
    existing_scan = supabase.table("scans").select("user_id").eq("id", request.scan_id).execute()
    if existing_scan.data:
        if existing_scan.data[0].get("user_id") != user.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to upload to this scan",
            )

    # Check user quota before issuing upload URL
    quota_check = await check_user_quota(user)
    if not quota_check.has_quota:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=quota_check.message or "No scans remaining. Upgrade your plan.",
        )

    if not settings.s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="S3 bucket not configured",
        )

    # Include user_id in path to isolate uploads by user
    s3_key = f"uploads/{user.sub}/{request.scan_id}.tar.gz"

    try:
        s3_client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=Config(signature_version="s3v4"),
        )

        # Generate presigned URL for PUT (upload)
        upload_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": s3_key,
                "ContentType": "application/gzip",
            },
            ExpiresIn=3600,  # 1 hour
        )

        return UploadUrlResponse(
            upload_url=upload_url,
            s3_key=s3_key,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate upload URL: {str(e)}",
        )


@router.get("/scans/{scan_id}/patch", response_model=PatchUrlResponse)
async def get_patch_url(
    scan_id: str,
    user: CurrentUser,
):
    """
    Get a presigned URL to download the patch file for a completed scan.

    The patch contains all file changes made during the scan.
    Only available for local_upload scans after completion.
    """
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    # Verify scan exists and belongs to user
    scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

    if not scan_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = scan_response.data

    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this scan",
        )

    if not settings.s3_bucket:
        return PatchUrlResponse(download_url=None, has_patch=False)

    # Path includes user_id for isolation (matches sandbox_service.py)
    patch_key = f"patches/{user.sub}/{scan_id}.patch"

    try:
        s3_client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=Config(signature_version="s3v4"),
        )

        # Check if patch exists
        try:
            s3_client.head_object(Bucket=settings.s3_bucket, Key=patch_key)
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return PatchUrlResponse(download_url=None, has_patch=False)
            raise

        # Generate presigned URL for GET (download)
        download_url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": patch_key,
            },
            ExpiresIn=3600,  # 1 hour
        )

        return PatchUrlResponse(
            download_url=download_url,
            has_patch=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get patch URL: {str(e)}",
        )


@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(
    scan_id: str,
    user: CurrentUser,
):
    """
    Cancel a running scan.

    Only the owner can cancel their scans.
    This stops the ECS task and updates the scan status to cancelled.
    """
    import structlog
    logger = structlog.get_logger()

    # Fetch scan to verify ownership
    scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

    if not scan_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = scan_response.data

    # Verify scan belongs to user
    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this scan",
        )

    # Check if scan is running
    if scan.get("status") not in ["running", "pending"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scan is not running (status: {scan.get('status')})",
        )

    # Stop the ECS task if we have a task ARN
    if scan.get("task_arn"):
        try:
            await sandbox_service.stop_task(scan.get("task_arn"))
        except Exception as e:
            logger.warning("Failed to stop ECS task", scan_id=scan_id, error=str(e))

    # Update scan status to cancelled
    supabase.table("scans").update({
        "status": "cancelled",
    }).eq("id", scan_id).execute()

    logger.info("Scan cancelled", scan_id=scan_id, user_id=user.sub)

    return {"success": True, "message": "Scan cancelled successfully"}


@router.delete("/scans/{scan_id}")
async def delete_scan(
    scan_id: str,
    user: CurrentUser,
):
    """
    Delete a scan and all associated logs.

    Only the owner can delete their scans.
    If the scan is running, it will be cancelled first.
    """
    import structlog
    logger = structlog.get_logger()

    try:
        # Fetch scan to verify ownership
        scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

        if not scan_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Scan not found",
            )

        scan = scan_response.data

        # Verify scan belongs to user
        if scan.get("user_id") != user.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to delete this scan",
            )

        # If scan is running, try to stop the ECS task
        if scan.get("status") == "running" and scan.get("task_arn"):
            try:
                await sandbox_service.stop_task(scan.get("task_arn"))
            except Exception as e:
                logger.warning("Failed to stop running task", scan_id=scan_id, error=str(e))

        # Delete scan logs first (foreign key constraint)
        supabase.table("scan_logs").delete().eq("scan_id", scan_id).execute()

        # Delete the scan
        supabase.table("scans").delete().eq("id", scan_id).execute()

        logger.info("Scan deleted", scan_id=scan_id, user_id=user.sub)

        return {"success": True, "message": "Scan deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete scan", scan_id=scan_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete scan: {str(e)}",
        )


@router.post("/scans/{scan_id}/replay")
async def replay_scan(
    scan_id: str,
    user: CurrentUser,
    speedup: int = 10,  # 10x faster by default
):
    """
    Replay a completed scan by streaming its historical logs in real-time.

    This endpoint:
    1. Fetches all scan_logs for the scan
    2. Streams them to Supabase realtime as events
    3. Makes the UI show the scan as if it's running live

    Args:
        speedup: How much faster to replay (default 10x)
    """
    import asyncio
    import structlog
    from datetime import datetime, timezone

    logger = structlog.get_logger()

    # Fetch scan to verify ownership
    scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

    if not scan_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = scan_response.data

    # Verify scan belongs to user
    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to replay this scan",
        )

    # Fetch all logs in chronological order
    logs_response = supabase.table("scan_logs").select("*").eq("scan_id", scan_id).order("created_at").execute()

    if not logs_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No logs found for this scan",
        )

    logs = logs_response.data
    total_logs = len(logs)

    logger.info("Starting scan replay", scan_id=scan_id, total_logs=total_logs, speedup=speedup)

    # Update scan status to "running" for UI
    supabase.table("scans").update({
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }).eq("id", scan_id).execute()

    # Stream logs with timing
    first_log_time = None
    last_real_time = None

    for i, log in enumerate(logs):
        log_time = datetime.fromisoformat(log["created_at"].replace("Z", "+00:00"))

        if first_log_time is None:
            first_log_time = log_time
            last_real_time = datetime.now(timezone.utc)
        else:
            # Calculate delay based on original timing
            time_since_first = (log_time - first_log_time).total_seconds()
            replay_delay = time_since_first / speedup

            # Sleep until we should send this log
            elapsed = (datetime.now(timezone.utc) - last_real_time).total_seconds()
            if replay_delay > elapsed:
                await asyncio.sleep(replay_delay - elapsed)

        # Insert event to trigger UI update
        supabase.table("scan_logs").insert({
            "scan_id": scan_id,
            "level": log.get("level", "info"),
            "message": log.get("message", ""),
            "agent_id": log.get("agent_id"),
            "metadata": log.get("metadata"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        # Log progress every 50 events
        if (i + 1) % 50 == 0:
            logger.info(f"Replay progress: {i+1}/{total_logs} logs streamed")

    # Restore original scan status
    supabase.table("scans").update({
        "status": scan.get("status"),
        "started_at": scan.get("started_at"),
        "completed_at": scan.get("completed_at"),
    }).eq("id", scan_id).execute()

    logger.info("Scan replay completed", scan_id=scan_id, total_logs=total_logs)

    return {
        "success": True,
        "message": f"Replayed {total_logs} logs at {speedup}x speed",
        "total_logs": total_logs,
    }


# PR Creation models
class CreatePRRequest(BaseModel):
    title: str | None = None
    description: str | None = None


class CreatePRResponse(BaseModel):
    success: bool
    pr_url: str | None = None
    pr_number: int | None = None
    error: str | None = None


@router.post("/scans/{scan_id}/create-pr", response_model=CreatePRResponse)
async def create_pr_for_scan(
    scan_id: str,
    user: CurrentUser,
    request: CreatePRRequest | None = None,
):
    """
    Create a Pull Request for a completed scan that has modified files.

    This endpoint:
    1. Verifies scan is completed and belongs to user
    2. Checks scan has modified files (has_modified_files flag)
    3. Gets GitHub App installation token
    4. Creates a PR with the security fixes

    Note: The scan must have has_modified_files=True and the pr_metadata
    must contain the branch and commit information from the scan.
    """
    import structlog
    logger = structlog.get_logger()

    # Fetch scan record
    scan_response = supabase.table("scans").select("*").eq("id", scan_id).single().execute()

    if not scan_response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    scan = scan_response.data

    # Verify scan belongs to user
    if scan.get("user_id") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create PR for this scan",
        )

    # Check scan is completed
    if scan.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scan is not completed (status: {scan.get('status')})",
        )

    # Check if PR already created
    if scan.get("pr_url"):
        return CreatePRResponse(
            success=True,
            pr_url=scan.get("pr_url"),
            error="PR already created for this scan",
        )

    # Check if scan has modified files
    if not scan.get("has_modified_files"):
        return CreatePRResponse(
            success=False,
            error="No modified files to commit. The scan did not make any code changes.",
        )

    # Get user's GitHub App installation
    profile_response = supabase.table("profiles").select(
        "github_app_installation_id"
    ).eq("id", user.sub).single().execute()

    if not profile_response.data or not profile_response.data.get("github_app_installation_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub App not installed. Please connect your GitHub account first.",
        )

    installation_id = profile_response.data["github_app_installation_id"]

    try:
        # Get GitHub App installation token
        github_token = await get_github_app_installation_token(installation_id)

        # Extract repo info from scan target
        target = scan.get("target", "")
        if target.startswith("https://github.com/"):
            repo_full_name = target.replace("https://github.com/", "").rstrip("/")
        elif target.startswith("github.com/"):
            repo_full_name = target.replace("github.com/", "").rstrip("/")
        else:
            repo_full_name = target

        # Get the fix branch from scan metadata (stored by tracer on scan completion)
        pr_metadata = scan.get("pr_metadata", {}) or {}
        fix_branch = pr_metadata.get("fix_branch", f"esprit-fix-{scan_id[:8]}")
        base_branch = scan.get("repo_branch") or "main"

        # Generate PR title and description
        vuln_count = scan.get("vulnerabilities_found", 0)
        pr_title = request.title if request and request.title else f"[Esprit] Security fixes - {vuln_count} vulnerabilities addressed"
        pr_description = request.description if request and request.description else f"""## Esprit Security Scan Results

This PR contains automated security fixes generated by Esprit.

### Summary
- **Scan ID:** {scan_id[:8]}
- **Vulnerabilities Found:** {vuln_count}
- **Critical:** {scan.get('critical_count', 0)}
- **High:** {scan.get('high_count', 0)}
- **Medium:** {scan.get('medium_count', 0)}
- **Low:** {scan.get('low_count', 0)}

### Changes
Security fixes have been applied to address the vulnerabilities discovered during the scan.

---
*Generated by [Esprit](https://esprit.security) - AI-powered penetration testing*
"""

        # Create PR via GitHub API
        async with httpx.AsyncClient() as client:
            pr_response = await client.post(
                f"https://api.github.com/repos/{repo_full_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={
                    "title": pr_title,
                    "body": pr_description,
                    "head": fix_branch,
                    "base": base_branch,
                },
            )

            if pr_response.status_code == 201:
                pr_data = pr_response.json()
                pr_url = pr_data.get("html_url")
                pr_number = pr_data.get("number")

                # Update scan with PR info
                supabase.table("scans").update({
                    "pr_url": pr_url,
                    "pr_created_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", scan_id).execute()

                logger.info("PR created", scan_id=scan_id, pr_url=pr_url, pr_number=pr_number)

                return CreatePRResponse(
                    success=True,
                    pr_url=pr_url,
                    pr_number=pr_number,
                )
            elif pr_response.status_code == 422:
                # PR might already exist or no changes
                error_data = pr_response.json()
                error_msg = error_data.get("message", "Unknown error")
                errors = error_data.get("errors", [])
                if errors:
                    error_msg = errors[0].get("message", error_msg)

                logger.warning("PR creation failed", scan_id=scan_id, error=error_msg)

                return CreatePRResponse(
                    success=False,
                    error=f"Could not create PR: {error_msg}",
                )
            else:
                logger.error("PR creation failed", scan_id=scan_id, status=pr_response.status_code, response=pr_response.text)
                return CreatePRResponse(
                    success=False,
                    error=f"GitHub API error: {pr_response.status_code}",
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create PR", scan_id=scan_id, error=str(e))
        return CreatePRResponse(
            success=False,
            error=f"Failed to create PR: {str(e)}",
        )


@router.get("/github/repos", response_model=GitHubReposResponse)
async def list_github_repos(user: CurrentUser):
    """
    List all GitHub repositories accessible via the user's GitHub App installation.

    Uses the GitHub App installation token to fetch repositories the user
    has granted access to.
    """
    # Get user's GitHub App installation ID from profile
    response = supabase.table("profiles").select(
        "github_app_installation_id"
    ).eq("id", user.sub).single().execute()

    if not response.data or not response.data.get("github_app_installation_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub App not installed. Please install the Esprit GitHub App to access repositories.",
        )

    installation_id = response.data["github_app_installation_id"]

    try:
        # Get installation access token
        access_token = await get_github_app_installation_token(installation_id)

        async with httpx.AsyncClient() as client:
            # Fetch repos accessible to this installation
            repos_response = await client.get(
                "https://api.github.com/installation/repositories",
                params={"per_page": 100},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            if repos_response.status_code == 401:
                # Installation token invalid - installation may have been removed
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="GitHub App installation expired or removed. Please reinstall the app.",
                )

            if repos_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to fetch repositories from GitHub",
                )

            data = repos_response.json()
            repos_data = data.get("repositories", [])

            repos = [
                GitHubRepoInfo(
                    id=repo["id"],
                    name=repo["name"],
                    full_name=repo["full_name"],
                    owner=repo["owner"]["login"],
                    html_url=repo["html_url"],
                    default_branch=repo.get("default_branch", "main"),
                    private=repo["private"],
                    description=repo.get("description"),
                )
                for repo in repos_data
            ]

            return GitHubReposResponse(repos=repos, total=len(repos))

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"GitHub API request failed: {str(e)}",
        )


# ============================================================================
# Device Authorization Flow (RFC 8628)
# Secure CLI authentication without localhost callback
# ============================================================================

import secrets
import string


def generate_device_code() -> str:
    """Generate a secure 32-character device code for CLI polling."""
    return secrets.token_urlsafe(24)  # 32 chars base64


def generate_user_code() -> str:
    """
    Generate a human-friendly 8-character user code (XXXX-XXXX).

    Uses only unambiguous characters (no 0/O, 1/l/I) for easy reading.
    """
    # Unambiguous uppercase letters and digits
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{part1}-{part2}"


# Device flow models
class DeviceCodeRequest(BaseModel):
    """Request to generate a new device code."""
    client_id: str = "esprit-cli"


class DeviceCodeResponse(BaseModel):
    """Response with device and user codes."""
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceTokenRequest(BaseModel):
    """Request to exchange device code for access token."""
    device_code: str
    grant_type: str = "urn:ietf:params:oauth:grant-type:device_code"


class DeviceTokenResponse(BaseModel):
    """Response with access token."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user_id: str
    email: str
    full_name: str = ""
    plan: str = "free"


class DeviceTokenErrorResponse(BaseModel):
    """Error response for device token polling."""
    error: str
    error_description: str


class DeviceAuthorizeRequest(BaseModel):
    """Request to authorize a device code (from web UI)."""
    user_code: str


class DeviceAuthorizeResponse(BaseModel):
    """Response after authorizing a device code."""
    success: bool
    message: str


@router.post("/auth/device/code", response_model=DeviceCodeResponse)
async def create_device_code(request: DeviceCodeRequest):
    """
    Generate a new device code for CLI authentication.

    This is the first step of the Device Authorization Flow (RFC 8628).
    The CLI calls this endpoint to get:
    - device_code: Secret code for polling (never shown to user)
    - user_code: Human-readable code for user to enter on web
    - verification_uri: URL where user enters the code

    No authentication required - anyone can request a device code.
    """
    import structlog
    logger = structlog.get_logger()

    device_code = generate_device_code()
    user_code = generate_user_code()

    # Codes expire in 15 minutes
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    try:
        # Store in database
        supabase.table("device_codes").insert({
            "device_code": device_code,
            "user_code": user_code,
            "status": "pending",
            "expires_at": expires_at.isoformat(),
        }).execute()

        logger.info("Device code created", user_code=user_code)

        return DeviceCodeResponse(
            device_code=device_code,
            user_code=user_code,
            verification_uri="https://esprit.dev/device",
            verification_uri_complete=f"https://esprit.dev/device?code={user_code}",
            expires_in=900,  # 15 minutes in seconds
            interval=5,  # Poll every 5 seconds
        )

    except Exception as e:
        logger.error("Failed to create device code", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create device code",
        )


@router.post("/auth/device/token")
async def exchange_device_token(request: DeviceTokenRequest):
    """
    Exchange a device code for an access token.

    The CLI polls this endpoint until the user authorizes the device code.

    Returns:
    - 200 with access_token if authorized
    - 400 with error="authorization_pending" if still waiting
    - 400 with error="expired_token" if code expired
    - 400 with error="access_denied" if user denied
    """
    import structlog
    logger = structlog.get_logger()

    try:
        # Look up device code
        response = supabase.table("device_codes").select("*").eq(
            "device_code", request.device_code
        ).execute()

        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_grant", "error_description": "Invalid device code"},
            )

        device = response.data[0]
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(device["expires_at"].replace("Z", "+00:00"))

        # Check if expired
        if now > expires_at or device["status"] == "expired":
            # Mark as expired if not already
            if device["status"] != "expired":
                supabase.table("device_codes").update({"status": "expired"}).eq(
                    "device_code", request.device_code
                ).execute()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "expired_token", "error_description": "Device code has expired"},
            )

        # Check if already used
        if device["status"] == "used":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_grant", "error_description": "Device code already used"},
            )

        # Check if still pending
        if device["status"] == "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "authorization_pending", "error_description": "User has not yet authorized"},
            )

        # Status is "authorized" - return the token
        if device["status"] == "authorized" and device.get("access_token"):
            # Mark as used
            supabase.table("device_codes").update({"status": "used"}).eq(
                "device_code", request.device_code
            ).execute()

            # Get user info including plan
            user_id = device.get("user_id")
            user_response = supabase.table("profiles").select("email, full_name, plan").eq("id", user_id).execute()
            profile_data = user_response.data[0] if user_response.data else {}
            email = profile_data.get("email", "")
            full_name = profile_data.get("full_name", "")
            plan = profile_data.get("plan", "free")

            logger.info("Device token exchanged", user_id=user_id)

            return DeviceTokenResponse(
                access_token=device["access_token"],
                token_type="Bearer",
                expires_in=3600 * 24 * 7,  # 7 days
                user_id=user_id,
                email=email,
                full_name=full_name,
                plan=plan,
            )

        # Unknown status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "server_error", "error_description": "Unknown device code status"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to exchange device token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "server_error", "error_description": str(e)},
        )


@router.post("/auth/device/authorize", response_model=DeviceAuthorizeResponse)
async def authorize_device_code(
    request: DeviceAuthorizeRequest,
    user: CurrentUser,
):
    """
    Authorize a device code (called from web UI after user logs in).

    This links the device code to the authenticated user and generates
    an access token that the CLI can retrieve via polling.
    """
    import structlog
    logger = structlog.get_logger()

    # Normalize user code (uppercase, ensure dash)
    user_code = request.user_code.upper().strip()
    if len(user_code) == 8 and "-" not in user_code:
        user_code = f"{user_code[:4]}-{user_code[4:]}"

    try:
        # Look up device code by user_code
        response = supabase.table("device_codes").select("*").eq(
            "user_code", user_code
        ).execute()

        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid code. Please check and try again.",
            )

        device = response.data[0]
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(device["expires_at"].replace("Z", "+00:00"))

        # Check if expired
        if now > expires_at:
            supabase.table("device_codes").update({"status": "expired"}).eq(
                "user_code", user_code
            ).execute()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Code has expired. Please request a new code from the CLI.",
            )

        # Check if already used or authorized
        if device["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Code has already been used. Please request a new code.",
            )

        # Generate access token for the CLI
        # We'll use Supabase to generate a proper JWT for the user
        # For now, we create a simple token that the CLI can use
        access_token = await _generate_cli_access_token(user.sub)

        # Update device code with authorization
        supabase.table("device_codes").update({
            "status": "authorized",
            "user_id": user.sub,
            "access_token": access_token,
            "authorized_at": now.isoformat(),
        }).eq("user_code", user_code).execute()

        logger.info("Device code authorized", user_id=user.sub, user_code=user_code)

        return DeviceAuthorizeResponse(
            success=True,
            message="Device authorized! You can close this page and return to your terminal.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to authorize device code", error=str(e), user_id=user.sub)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to authorize device: {str(e)}",
        )


async def _generate_cli_access_token(user_id: str) -> str:
    """
    Generate an access token for CLI use.

    This creates a JWT token that the CLI can use for API calls.
    The token is signed with the Supabase JWT secret.
    """
    import base64
    import time

    from jose import jwt

    import structlog
    logger = structlog.get_logger()

    try:
        # Extract JWT secret from Supabase service key
        # The service key is a JWT itself - we need the secret used to sign it
        # For Supabase, we use the JWT_SECRET from environment or derive from service key
        jwt_secret = settings.supabase_jwt_secret if hasattr(settings, 'supabase_jwt_secret') else None

        if not jwt_secret:
            # Fallback: use a portion of service key as secret (not ideal but works)
            # In production, set SUPABASE_JWT_SECRET environment variable
            jwt_secret = settings.supabase_service_key[:32]

        now = int(time.time())
        payload = {
            "sub": user_id,
            "role": "authenticated",
            "iat": now,
            "exp": now + (7 * 24 * 3600),  # 7 days
            "aud": "authenticated",
            "iss": "esprit-cli",
        }

        token = jwt.encode(payload, jwt_secret, algorithm="HS256")
        logger.info("Generated CLI access token", user_id=user_id)
        return token

    except Exception as e:
        logger.error("Failed to generate CLI token", error=str(e))
        raise
