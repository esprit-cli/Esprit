"""
Authentication utilities for validating Supabase JWT tokens.
"""

from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import create_client

from app.core.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

supabase = create_client(settings.supabase_url, settings.supabase_service_key)

security = HTTPBearer()


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # User ID
    email: str | None = None
    role: str = "authenticated"
    exp: int


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TokenPayload:
    """Validate JWT token by introspecting it with Supabase Auth."""
    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_response = supabase.auth.get_user(jwt=token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase token validation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = getattr(user_response, "user", None)
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenPayload(
        sub=str(user_id),
        email=getattr(user, "email", None),
        role="authenticated",
        exp=0,
    )


CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
