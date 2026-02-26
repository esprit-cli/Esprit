"""
Configuration settings for the Esprit Backend service.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "Esprit Backend"
    debug: bool = False
    environment: Literal["development", "staging", "production", "prod", "dev"] = "development"

    # Supabase
    supabase_url: str
    supabase_service_key: str  # Service role key for backend operations

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    # ECS Configuration
    ecs_cluster_name: str = "esprit-sandboxes"
    ecs_task_definition: str = "esprit-sandbox"
    ecs_subnets: list[str] = []
    ecs_security_groups: list[str] = []

    # LLM Configuration
    # Using AWS Bedrock - credentials from ECS task role, no API key needed
    # Model uses cross-region inference profile (us. prefix required)
    llm_provider: str = "bedrock"
    llm_api_key: str = ""  # Not needed for Bedrock
    llm_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # Rate Limiting
    redis_url: str = "redis://localhost:6379"

    # GitHub OAuth (for repo integration)
    github_client_id: str = ""
    github_client_secret: str = ""

    # GitHub App (for repo access)
    github_app_id: str = ""
    github_app_private_key: str = ""  # PEM format private key
    github_app_client_id: str = ""
    github_app_client_secret: str = ""

    # S3 Configuration (for scan results)
    s3_bucket: str = ""

    # Security Controls
    allow_quota_bypass: bool = False
    quota_bypass_code: str = ""
    supabase_jwt_secret: str = ""
    auth_jwt_secret: str = ""
    llm_requests_per_minute: int = 120

    # Plan Limits
    free_scans_per_month: int = 0  # Legacy field, free now uses one lifetime claim.
    free_tokens_per_month: int = 0  # Legacy field, free now uses free_single_scan_tokens.
    free_lifetime_scans: int = 1
    free_single_scan_tokens: int = 1_000_000
    pro_scans_per_month: int = 10  # $10/month
    pro_tokens_per_month: int = 1_000_000
    team_scans_per_month: int = 999999  # Unlimited
    team_tokens_per_month: int = 10_000_000


# Scan Tier Configuration
# Each tier defines iteration limits and timeouts
# Budget constraints removed (2024-12-07) to allow thorough dynamic testing
#
# Iteration Rationale:
# - Local CLI uses 300 iterations with no limits
# - Cloud now matches local behavior for feature parity
# - Increased iterations allow: clone → analyze → npm install → npm run dev → test endpoints
#
SCAN_TIERS = {
    'quick': {
        'max_iterations': 10000,        # High limit - budget/timeout are real limiters
        'max_duration_seconds': 14400,  # 4 hours hard limit (safety guard)
        'llm_timeout_seconds': 120,     # 2 minutes per LLM call
        'budget_usd': 999.00,           # Effectively unlimited
        'description': 'Quick security scan',
    },
    'standard': {
        'max_iterations': 10000,        # High limit - budget/timeout are real limiters
        'max_duration_seconds': 14400,  # 4 hours hard limit (safety guard)
        'llm_timeout_seconds': 300,     # 5 minutes per LLM call
        'budget_usd': 999.00,           # Effectively unlimited
        'description': 'Standard security scan with dynamic testing',
    },
    'deep': {
        'max_iterations': 10000,        # High limit - budget/timeout are real limiters
        'max_duration_seconds': 14400,  # 4 hours hard limit (safety guard)
        'llm_timeout_seconds': 600,     # 10 minutes per LLM call
        'budget_usd': 999.00,           # Effectively unlimited
        'description': 'Comprehensive penetration test',
    },
}


def get_scan_tier_config(scan_type: str) -> dict:
    """Get configuration for a scan tier. Defaults to 'standard' if unknown."""
    return SCAN_TIERS.get(scan_type, SCAN_TIERS['standard'])


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Export settings instance for easy importing
settings = get_settings()
