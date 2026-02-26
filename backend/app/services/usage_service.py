"""
Usage tracking and rate limiting service.

Tracks scan counts and token usage per user per month.
"""

import hmac
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

    @staticmethod
    def free_available_models() -> list[str]:
        """Cloud models exposed to free users."""
        return ["default", "haiku"]

    def _get_current_month(self) -> str:
        """Get current month in YYYY-MM format."""
        return datetime.now(tz=timezone.utc).strftime("%Y-%m")

    def _is_quota_bypass_allowed(self, bypass_code: str | None) -> bool:
        """Validate emergency bypass code when explicitly enabled."""
        if not settings.allow_quota_bypass:
            return False
        expected_code = settings.quota_bypass_code.strip()
        if not expected_code or not bypass_code:
            return False
        return hmac.compare_digest(expected_code, bypass_code)

    async def get_user_plan(self, user_id: str) -> str:
        """Get user's current plan."""
        response = self.supabase.table("profiles").select("plan").eq("id", user_id).single().execute()

        if response.data:
            return str(response.data.get("plan", "free")).strip().lower()
        return "free"

    async def get_usage(self, user_id: str) -> UsageResponse:
        """Get user's current month usage."""
        month = self._get_current_month()
        plan = await self.get_user_plan(user_id)
        limits = self.plan_limits.get(plan, self.plan_limits["free"])
        if plan == "free":
            limits = {
                "scans": settings.free_lifetime_scans,
                "tokens": settings.free_single_scan_tokens,
            }

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

        scans_used = usage.get("scans_count", 0)
        if plan == "free":
            claim = await self.get_free_scan_claim(user_id)
            scans_used = 1 if claim else 0

        return UsageResponse(
            scans_used=scans_used,
            scans_limit=limits["scans"],
            tokens_used=usage.get("tokens_used", 0),
            tokens_limit=limits["tokens"],
            month=month,
            plan=plan,
        )

    async def get_free_scan_claim(self, user_id: str) -> dict | None:
        """Return free scan claim row if one exists."""
        response = (
            self.supabase.table("free_scan_claims")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
        return None

    async def claim_free_scan(self, user_id: str, scan_id: str) -> tuple[bool, str | None]:
        """Attempt to atomically claim the single free scan slot."""
        payload = {
            "user_id": user_id,
            "scan_id": scan_id,
            "claimed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            self.supabase.table("free_scan_claims").insert(payload).execute()
            logger.info("Claimed free scan", user_id=user_id, scan_id=scan_id)
            return True, None
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if any(token in message for token in ["duplicate", "unique", "already"]):
                return False, "Free scan already used."
            logger.error("Failed to claim free scan", user_id=user_id, scan_id=scan_id, error=str(exc))
            raise

    async def check_quota(
        self,
        user_id: str,
        bypass_code: str | None = None,
        *,
        enforce_scan_limit: bool = True,
    ) -> QuotaCheckResponse:
        """Check if user has remaining quota for a new scan."""
        if self._is_quota_bypass_allowed(bypass_code):
            logger.info("Quota bypass approved", user_id=user_id)
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

        if usage.plan == "free":
            if enforce_scan_limit and scans_remaining <= 0:
                return QuotaCheckResponse(
                    has_quota=False,
                    scans_remaining=0,
                    tokens_remaining=max(tokens_remaining, 0),
                    message="Your free Esprit cloud scan has already been used.",
                )
            if tokens_remaining <= 0:
                return QuotaCheckResponse(
                    has_quota=False,
                    scans_remaining=max(scans_remaining, 0),
                    tokens_remaining=0,
                    message="Your free scan reached the token limit.",
                )
            return QuotaCheckResponse(
                has_quota=True,
                scans_remaining=max(scans_remaining, 0),
                tokens_remaining=tokens_remaining,
                message=None,
            )

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
