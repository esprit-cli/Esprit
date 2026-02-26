"""Correlate endpoints discovered from mobile app analysis with proxy traffic.

Identifies untested API endpoints extracted from mobile artifacts that don't
appear in captured proxy traffic — these are high-value testing targets.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from .models import EvidenceRef, Hypothesis


logger = logging.getLogger(__name__)

# Common API path patterns found in mobile apps
_API_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>\]\)]+",
    re.IGNORECASE,
)

# Filter out non-API URLs (assets, SDKs, tracking)
_EXCLUDE_DOMAINS = {
    "googleapis.com",
    "google.com",
    "apple.com",
    "facebook.com",
    "fbcdn.net",
    "crashlytics.com",
    "firebase.io",
    "firebaseio.com",
    "gstatic.com",
    "cloudfront.net",
    "amazonaws.com",
    "sentry.io",
    "mixpanel.com",
    "amplitude.com",
    "branch.io",
    "appsflyer.com",
    "adjust.com",
    "segment.io",
    "segment.com",
}

_EXCLUDE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi",
    ".zip", ".gz", ".tar",
}


class EndpointCorrelator:
    """Correlates mobile-extracted endpoints with observed proxy traffic."""

    def __init__(self) -> None:
        self._extracted_endpoints: list[dict[str, str]] = []
        self._observed_endpoints: set[str] = set()

    def extract_endpoints_from_text(
        self, text: str, source_file: str = ""
    ) -> list[dict[str, str]]:
        """Extract API endpoint URLs from decompiled source text."""
        endpoints: list[dict[str, str]] = []
        seen: set[str] = set()

        for match in _API_URL_PATTERN.finditer(text):
            url = match.group().rstrip(".,;:\"')")
            normalized = _normalize_url(url)

            if not normalized or normalized in seen:
                continue

            if _should_exclude_url(url):
                continue

            seen.add(normalized)
            endpoints.append({
                "url": url,
                "normalized": normalized,
                "source_file": source_file,
            })

        self._extracted_endpoints.extend(endpoints)
        return endpoints

    def register_observed_endpoint(self, method: str, host: str, path: str) -> None:
        """Register an endpoint observed in proxy traffic."""
        normalized = _normalize_path(f"{host}{path}".lower())
        self._observed_endpoints.add(normalized)

    def register_observed_endpoints_from_requests(
        self, requests_data: list[dict[str, Any]]
    ) -> int:
        """Register endpoints from a list_requests result."""
        count = 0
        for req in requests_data:
            if not isinstance(req, dict):
                continue
            host = req.get("host", "")
            path = req.get("path", "")
            method = req.get("method", "")
            if host and path:
                self.register_observed_endpoint(method, host, path)
                count += 1
        return count

    def find_untested_endpoints(self) -> list[dict[str, str]]:
        """Find extracted endpoints not seen in proxy traffic."""
        untested: list[dict[str, str]] = []

        for endpoint in self._extracted_endpoints:
            normalized = endpoint["normalized"]
            parsed = urlparse(endpoint["url"])
            host_path = f"{parsed.hostname or ''}{parsed.path}".lower()
            host_path_normalized = _normalize_path(host_path)

            if host_path_normalized not in self._observed_endpoints:
                untested.append(endpoint)

        return untested

    def generate_hypotheses_for_untested(self) -> list[Hypothesis]:
        """Generate hypotheses for untested endpoints found in mobile app."""
        untested = self.find_untested_endpoints()
        hypotheses: list[Hypothesis] = []

        for endpoint in untested:
            url = endpoint["url"]
            source = endpoint.get("source_file", "mobile_app")

            hypothesis = Hypothesis(
                title=f"Untested mobile API endpoint: {_truncate(url, 60)}",
                source="mobile_endpoint_extraction",
                target=url,
                vulnerability_class="Untested Endpoint",
                novelty_score=0.85,
                impact_score=0.60,
                evidence_score=0.50,
                reachability_score=0.80,
                evidence_refs=[
                    EvidenceRef(
                        source="mobile_static",
                        ref_id=source,
                        description=f"Endpoint found in {source}",
                    )
                ],
            )
            hypotheses.append(hypothesis)

        return hypotheses

    @property
    def extracted_count(self) -> int:
        return len(self._extracted_endpoints)

    @property
    def observed_count(self) -> int:
        return len(self._observed_endpoints)


def _normalize_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/").lower()
        # Replace numeric IDs with placeholder
        path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
        # Replace UUIDs
        path = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{id}",
            path,
        )
        host = (parsed.hostname or "").lower()
        return f"{host}{path}"
    except (ValueError, AttributeError):
        return url.lower()


def _normalize_path(path: str) -> str:
    """Normalize a host+path for comparison."""
    path = path.rstrip("/").lower()
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    path = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "/{id}",
        path,
    )
    return path


def _should_exclude_url(url: str) -> bool:
    """Check if a URL should be excluded from endpoint analysis."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        # Exclude known SDK/tracking domains
        for domain in _EXCLUDE_DOMAINS:
            if hostname.endswith(domain):
                return True

        # Exclude static assets
        path_lower = parsed.path.lower()
        for ext in _EXCLUDE_EXTENSIONS:
            if path_lower.endswith(ext):
                return True

    except (ValueError, AttributeError):
        pass

    return False


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
