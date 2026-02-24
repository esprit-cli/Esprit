"""
Pydantic schemas for API request/response models.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# Sandbox Schemas
class SandboxCreateRequest(BaseModel):
    """Request to create a new sandbox."""

    scan_id: str
    target: str
    target_type: Literal["url", "repository", "public_repository", "local_upload"]
    scan_type: Literal["deep", "quick", "compliance"]
    test_username: str | None = None  # Optional credentials for authenticated testing
    test_password: str | None = None  # Optional credentials for authenticated testing
    bypass_code: str | None = None  # Optional code to bypass quota limits


class SandboxCreateResponse(BaseModel):
    """Response after creating a sandbox."""

    sandbox_id: str
    status: str = "creating"
    tool_server_url: str | None = None
    expires_at: datetime | None = None


class SandboxStatusResponse(BaseModel):
    """Sandbox status response."""

    sandbox_id: str
    status: Literal["creating", "running", "stopped", "failed"]
    tool_server_url: str | None = None
    public_ip: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None


# LLM Proxy Schemas
class LLMGenerateRequest(BaseModel):
    """Request for LLM generation."""

    messages: list[dict]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    scan_id: str | None = None  # For usage tracking
    tools: list[dict] | None = None
    reasoning_effort: str | None = None


class LLMGenerateResponse(BaseModel):
    """Response from LLM generation."""

    content: str
    model: str
    tokens_used: int
    finish_reason: str | None = None
    tool_calls: list[dict] | None = None
    thinking_blocks: list[dict] | None = None


# Usage Schemas
class UsageResponse(BaseModel):
    """User's current usage stats."""

    scans_used: int
    scans_limit: int
    tokens_used: int
    tokens_limit: int
    month: str
    plan: str


class QuotaCheckResponse(BaseModel):
    """Check if user has remaining quota."""

    has_quota: bool
    scans_remaining: int
    tokens_remaining: int
    message: str | None = None


# Scan Schemas
class ScanCreateRequest(BaseModel):
    """Request to create a new scan."""

    target: str
    target_type: Literal["url", "repository", "public_repository", "local_upload"]
    scan_type: Literal["standard", "deep", "quick", "compliance"] = "standard"
    instruction: str | None = None


class ScanCreateResponse(BaseModel):
    """Response after creating a scan."""

    scan_id: str
    status: str = "pending"
    message: str = "Scan created successfully"


class ScanStatusResponse(BaseModel):
    """Response with scan status and details."""

    scan_id: str
    status: str
    target: str
    target_type: str
    scan_type: str
    vulnerabilities_found: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScanLogEntry(BaseModel):
    """Single log entry."""

    id: str
    created_at: datetime
    log_type: str
    content: str


class ScanLogsResponse(BaseModel):
    """Response with scan logs."""

    scan_id: str
    logs: list[ScanLogEntry]


class ScanUpdateRequest(BaseModel):
    """Request to update scan status."""

    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    vulnerabilities_found: int | None = None
    critical_count: int | None = None
    high_count: int | None = None
    medium_count: int | None = None
    low_count: int | None = None
    error_message: str | None = None


# Webhook/Callback Schemas
class ScanCompleteWebhook(BaseModel):
    """Webhook payload when a scan completes."""

    scan_id: str
    status: str
    vulnerabilities_found: int
    report_url: str | None = None
    duration_seconds: int
