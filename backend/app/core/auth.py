"""
Authentication utilities for validating Supabase JWT tokens.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()

# Supabase JWT secret is derived from the project
# In production, get this from Supabase dashboard -> Settings -> API -> JWT Secret
SUPABASE_JWT_SECRET = settings.supabase_service_key.split(".")[1] if settings.supabase_service_key else ""

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
    """
    Validate JWT token and return user payload.

    In production, this should verify against Supabase's JWT secret.
    """
    token = credentials.credentials

    try:
        # For development, we'll decode without verification
        # In production, use proper JWT verification with Supabase
        payload = jwt.decode(
            token,
            key="",  # Not used when verify_signature is False
            algorithms=["HS256", "RS256"],
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
        )

        return TokenPayload(
            sub=payload.get("sub", ""),
            email=payload.get("email"),
            role=payload.get("role", "authenticated"),
            exp=payload.get("exp", 0),
        )
    except JWTError as e:
        import structlog
        logger = structlog.get_logger()
        logger.error("JWT decode error", error=str(e), token_prefix=token[:50] if token else None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
