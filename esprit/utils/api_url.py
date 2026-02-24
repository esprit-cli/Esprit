from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse


DEFAULT_ESPRIT_API_BASE_URL = "https://esprit.dev/api/v1"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _allow_insecure_local_http() -> bool:
    value = (os.getenv("ESPRIT_ALLOW_INSECURE_LOCAL_API") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_esprit_api_base_url() -> str:
    """Return validated Esprit API base URL."""
    raw = (os.getenv("ESPRIT_API_URL") or DEFAULT_ESPRIT_API_BASE_URL).strip()
    parsed = urlparse(raw)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("ESPRIT_API_URL must be an absolute URL (scheme + host).")

    if parsed.username or parsed.password:
        raise ValueError("ESPRIT_API_URL must not contain embedded credentials.")

    if parsed.query or parsed.fragment:
        raise ValueError("ESPRIT_API_URL must not contain query parameters or fragments.")

    hostname = (parsed.hostname or "").strip().lower()
    is_local_host = hostname in _LOCAL_HOSTS

    if parsed.scheme != "https":
        if not (
            parsed.scheme == "http"
            and is_local_host
            and _allow_insecure_local_http()
        ):
            raise ValueError(
                "ESPRIT_API_URL must use HTTPS. "
                "For local HTTP development, set ESPRIT_ALLOW_INSECURE_LOCAL_API=1."
            )

    normalized_path = parsed.path.rstrip("/")
    normalized = urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))
    return normalized.rstrip("/")


def is_trusted_runtime_url(api_base_url: str, candidate_url: str) -> bool:
    """Validate runtime URL returned by API to prevent token exfiltration via untrusted hosts."""
    try:
        api_parsed = urlparse(api_base_url)
        candidate_parsed = urlparse(candidate_url)
    except ValueError:
        return False

    if (
        not candidate_parsed.scheme
        or not candidate_parsed.netloc
        or candidate_parsed.username
        or candidate_parsed.password
        or candidate_parsed.query
        or candidate_parsed.fragment
    ):
        return False

    api_host = (api_parsed.hostname or "").lower()
    candidate_host = (candidate_parsed.hostname or "").lower()
    if not api_host or not candidate_host:
        return False

    is_local_pair = api_host in _LOCAL_HOSTS and candidate_host in _LOCAL_HOSTS
    if candidate_parsed.scheme != "https":
        if not (
            candidate_parsed.scheme == "http"
            and is_local_pair
            and _allow_insecure_local_http()
        ):
            return False

    if candidate_host == api_host or candidate_host.endswith(f".{api_host}"):
        return True

    api_parts = api_host.split(".")
    candidate_parts = candidate_host.split(".")
    if len(api_parts) < 2 or len(candidate_parts) < 2:
        return False

    api_reg_domain = ".".join(api_parts[-2:])
    candidate_reg_domain = ".".join(candidate_parts[-2:])
    return api_reg_domain == candidate_reg_domain
