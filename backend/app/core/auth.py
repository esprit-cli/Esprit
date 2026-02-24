"""
Authentication utilities for validating Supabase JWT tokens.
"""

import time
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

security = HTTPBearer()


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # User ID
    email: str | None = None
    role: str = "authenticated"
    exp: int


def _unauthorized(detail: str = "Invalid authentication token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _normalize_payload(payload: dict[str, object]) -> TokenPayload:
    sub = payload.get("sub")
    exp = payload.get("exp")
    aud = payload.get("aud")
    iss = payload.get("iss")

    if not isinstance(sub, str) or not sub.strip():
        raise _unauthorized()
    if not isinstance(exp, (int, float)):
        raise _unauthorized("Authentication token missing expiration.")
    if int(exp) <= int(time.time()):
        raise _unauthorized("Authentication token expired.")
    if aud not in ("authenticated", None):
        raise _unauthorized("Authentication token audience is invalid.")
    if isinstance(iss, str) and iss.strip() and iss not in {"esprit-cli"}:
        # Keep strict issuer allowlist for CLI-issued access tokens.
        raise _unauthorized("Authentication token issuer is invalid.")

    role = payload.get("role", "authenticated")
    email = payload.get("email")
    return TokenPayload(
        sub=sub.strip(),
        email=email if isinstance(email, str) else None,
        role=role if isinstance(role, str) else "authenticated",
        exp=int(exp),
    )


def _candidate_jwt_secrets() -> list[str]:
    """Return ordered JWT secrets for signature validation."""
    secrets: list[str] = []
    if settings.auth_jwt_secret:
        secrets.append(settings.auth_jwt_secret)
    if settings.supabase_jwt_secret and settings.supabase_jwt_secret not in secrets:
        secrets.append(settings.supabase_jwt_secret)
    if settings.supabase_service_key:
        legacy_secret = settings.supabase_service_key[:32]
        if legacy_secret and legacy_secret not in secrets:
            secrets.append(legacy_secret)
    return secrets


def _decode_payload(token: str) -> dict[str, object]:
    last_error: JWTError | None = None
    for secret in _candidate_jwt_secrets():
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            if isinstance(payload, dict):
                return payload
            break
        except JWTError as exc:
            last_error = exc

    if last_error is not None:
        logger.warning("JWT parse/verify failed", error=str(last_error))
        raise _unauthorized() from last_error
    logger.warning("JWT parse/verify failed", error="No configured JWT secret")
    raise _unauthorized()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TokenPayload:
    """Validate JWT token for Esprit cloud API access."""
    token = credentials.credentials.strip()
    if not token:
        raise _unauthorized()

    payload = _decode_payload(token)
    return _normalize_payload(payload)


CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
