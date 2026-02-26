"""
Authentication utilities for validating Supabase JWT tokens.
"""

import time
from typing import Annotated, Any

import requests
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()
logger = structlog.get_logger()

security = HTTPBearer()
_TOKEN_CACHE_TTL_SECONDS = 120
_token_cache: dict[str, tuple[float, dict[str, object]]] = {}


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
    if isinstance(iss, str) and iss.strip():
        normalized_iss = iss.strip().rstrip("/")
        if normalized_iss not in _allowed_issuers():
            raise _unauthorized("Authentication token issuer is invalid.")

    role = payload.get("role", "authenticated")
    email = payload.get("email")
    return TokenPayload(
        sub=sub.strip(),
        email=email if isinstance(email, str) else None,
        role=role if isinstance(role, str) else "authenticated",
        exp=int(exp),
    )


def _allowed_issuers() -> set[str]:
    allowed = {"esprit-cli"}
    supabase_url = settings.supabase_url.strip().rstrip("/")
    if not supabase_url:
        return allowed
    allowed.add(supabase_url)
    allowed.add(f"{supabase_url}/auth/v1")
    return allowed


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


def _extract_unverified_claims(token: str) -> dict[str, Any]:
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError:
        return {}
    return claims if isinstance(claims, dict) else {}


def _decode_via_supabase_userinfo(token: str) -> dict[str, object] | None:
    """Fallback validation path for Supabase tokens (e.g., asymmetric signing)."""
    now = time.time()
    cached = _token_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]

    supabase_url = settings.supabase_url.strip().rstrip("/")
    if not supabase_url or not settings.supabase_service_key:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": settings.supabase_service_key,
    }
    try:
        response = requests.get(
            f"{supabase_url}/auth/v1/user",
            headers=headers,
            timeout=5,
        )
    except requests.RequestException as exc:
        logger.warning("Supabase user verification request failed", error=str(exc))
        return None

    if response.status_code != 200:
        logger.warning("Supabase user verification rejected token", status_code=response.status_code)
        return None

    user_info = response.json()
    if not isinstance(user_info, dict):
        return None

    claims = _extract_unverified_claims(token)
    exp_value = claims.get("exp")
    if not isinstance(exp_value, (int, float)):
        exp_value = int(now) + _TOKEN_CACHE_TTL_SECONDS
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or not issuer.strip():
        issuer = f"{supabase_url}/auth/v1"

    payload: dict[str, object] = {
        "sub": user_info.get("id") or claims.get("sub"),
        "email": user_info.get("email") or claims.get("email"),
        "role": claims.get("role", "authenticated"),
        "exp": int(exp_value),
        "aud": claims.get("aud", "authenticated"),
        "iss": issuer,
    }

    cache_expiry = min(now + _TOKEN_CACHE_TTL_SECONDS, float(payload["exp"]))
    if cache_expiry > now:
        _token_cache[token] = (cache_expiry, payload)
    return payload


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

    supabase_payload = _decode_via_supabase_userinfo(token)
    if isinstance(supabase_payload, dict):
        return supabase_payload

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
