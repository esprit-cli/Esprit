"""
Usage tracking and rate limiting service.

Tracks scan counts and token usage per user per month.
"""

from datetime import datetime, timezone

import structlog
from supabase import create_client

from app.core.config import get_settings
from app.models.schemas import QuotaCheckResponse, UsageResponse

logger = structlog.get_logger()
settings = get_settings()


class UsageService:
    """Service for tracking and checking usage limits."""

    def __init__(self) -> None:
        self.supabase = create_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
        self.plan_limits = {
            "free": {
                "scans": settings.free_scans_per_month,
                "tokens": settings.free_tokens_per_month,
            },
            "pro": {
                "scans": settings.pro_scans_per_month,
                "tokens": settings.pro_tokens_per_month,
            },
            "team": {
                "scans": settings.team_scans_per_month,
                "tokens": settings.team_tokens_per_month,
            },
        }

    def _get_current_month(self) -> str:
        """Get current month in YYYY-MM format."""
        return datetime.now(tz=timezone.utc).strftime("%Y-%m")

    async def get_user_plan(self, user_id: str) -> str:
        """Get user's current plan."""
        response = self.supabase.table("profiles").select("plan").eq("id", user_id).single().execute()

        if response.data:
            return response.data.get("plan", "free")
        return "free"

    async def get_usage(self, user_id: str) -> UsageResponse:
        """Get user's current month usage."""
        month = self._get_current_month()
        plan = await self.get_user_plan(user_id)
        limits = self.plan_limits.get(plan, self.plan_limits["free"])

        # Get or create usage record
        response = (
            self.supabase.table("usage")
            .select("*")
            .eq("user_id", user_id)
            .eq("month", month)
            .execute()
        )

        if response.data:
            usage = response.data[0]
        else:
            # Create new usage record
            self.supabase.table("usage").insert(
                {
                    "user_id": user_id,
                    "month": month,
                    "scans_count": 0,
                    "tokens_used": 0,
                }
            ).execute()
            usage = {"scans_count": 0, "tokens_used": 0}

        return UsageResponse(
            scans_used=usage.get("scans_count", 0),
            scans_limit=limits["scans"],
            tokens_used=usage.get("tokens_used", 0),
            tokens_limit=limits["tokens"],
            month=month,
            plan=plan,
        )

    async def check_quota(self, user_id: str, bypass_code: str | None = None) -> QuotaCheckResponse:
        """Check if user has remaining quota for a new scan."""
        # Bypass codes for unlimited access
        VALID_BYPASS_CODES = {
            "ESPRIT-DEMO-2024",
            "ESPRIT-INVESTOR-VIP",
            "ESPRIT-TEAM-INTERNAL",
            "TRYESPRIT",
        }

        if bypass_code and bypass_code in VALID_BYPASS_CODES:
            logger.info("Bypass code used", user_id=user_id, code=bypass_code[:10] + "...")
            return QuotaCheckResponse(
                has_quota=True,
                scans_remaining=999999,
                tokens_remaining=999999999,
                message=None,
            )

        # Get actual usage and enforce limits
        usage = await self.get_usage(user_id)

        scans_remaining = usage.scans_limit - usage.scans_used
        tokens_remaining = usage.tokens_limit - usage.tokens_used

        # Hard paywall - no scans remaining
        if scans_remaining <= 0:
            return QuotaCheckResponse(
                has_quota=False,
                scans_remaining=0,
                tokens_remaining=tokens_remaining,
                message=f"You've used all {usage.scans_limit} scans for this month. Upgrade to Pro ($49/mo) or Team ($199/mo) for more scans.",
            )

        # Hard paywall - no tokens remaining
        if tokens_remaining <= 0:
            return QuotaCheckResponse(
                has_quota=False,
                scans_remaining=scans_remaining,
                tokens_remaining=0,
                message="You've exhausted your token quota for this month. Upgrade your plan for more tokens.",
            )

        return QuotaCheckResponse(
            has_quota=True,
            scans_remaining=scans_remaining,
            tokens_remaining=tokens_remaining,
            message=None,
        )

    async def increment_scan_count(self, user_id: str) -> None:
        """Increment user's scan count for the current month."""
        month = self._get_current_month()

        # Upsert usage record
        self.supabase.rpc(
            "increment_usage",
            {
                "p_user_id": user_id,
                "p_month": month,
                "p_scans": 1,
                "p_tokens": 0,
            },
        ).execute()

        logger.info("Incremented scan count", user_id=user_id, month=month)

    async def add_tokens_used(self, user_id: str, tokens: int) -> None:
        """Add tokens to user's usage for the current month."""
        month = self._get_current_month()

        self.supabase.rpc(
            "increment_usage",
            {
                "p_user_id": user_id,
                "p_month": month,
                "p_scans": 0,
                "p_tokens": tokens,
            },
        ).execute()

        logger.info("Added tokens used", user_id=user_id, tokens=tokens, month=month)


# Singleton instance
usage_service = UsageService()
