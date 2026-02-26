"""Tool-layer scope enforcement for autonomous discovery.

Validates that outbound testing requests stay within allowed scope,
derived from target definitions. This is a deterministic guard that
does not rely on prompt instructions.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class ScopeGuard:
    """Enforce request scope based on target definitions.

    Blocks or warns when autonomous discovery attempts to test hosts
    not in the allowed scope.
    """

    def __init__(self, mode: str = "warn") -> None:
        """Initialize scope guard.

        Args:
            mode: "block" to reject out-of-scope requests,
                  "warn" to log a warning but allow (default).
        """
        self._allowed_hosts: set[str] = set()
        self._allowed_patterns: list[str] = []
        self._mode = mode

    def register_targets(self, targets_info: list[dict[str, Any]]) -> None:
        """Extract allowed hosts from scan target definitions."""
        for target in targets_info:
            target_type = target.get("type", "")
            details = target.get("details", {})

            if target_type == "web_application":
                url = details.get("target_url", "")
                host = self._extract_host(url)
                if host:
                    self._allowed_hosts.add(host)

            elif target_type == "ip_address":
                ip = details.get("target_ip", "")
                if ip:
                    self._allowed_hosts.add(ip.lower())

            elif target_type == "mobile_app":
                # Mobile apps may discover API hosts during analysis —
                # those get added dynamically via add_allowed_host
                pass

    def add_allowed_host(self, host: str) -> None:
        """Dynamically add a host to the allowed scope."""
        normalized = host.lower().strip()
        if normalized:
            self._allowed_hosts.add(normalized)

    def add_allowed_hosts_from_proxy(self, requests_data: list[dict[str, Any]]) -> int:
        """Add hosts seen in proxy traffic to allowed scope."""
        count = 0
        for req in requests_data:
            if not isinstance(req, dict):
                continue
            host = req.get("host", "")
            if host:
                normalized = host.lower().strip()
                if normalized not in self._allowed_hosts:
                    self._allowed_hosts.add(normalized)
                    count += 1
        return count

    def check_url(self, url: str) -> ScopeCheckResult:
        """Check if a URL is within allowed scope."""
        if not self._allowed_hosts:
            # No scope defined — allow everything (matches pre-autonomy behavior)
            return ScopeCheckResult(allowed=True, reason="no_scope_defined")

        host = self._extract_host(url)
        if not host:
            return ScopeCheckResult(
                allowed=False,
                reason="invalid_url",
                message=f"Could not extract host from URL: {url}",
            )

        if host in self._allowed_hosts:
            return ScopeCheckResult(allowed=True, reason="host_in_scope")

        # Check if it's a subdomain of an allowed host
        for allowed in self._allowed_hosts:
            if host.endswith(f".{allowed}"):
                return ScopeCheckResult(allowed=True, reason="subdomain_in_scope")

        message = f"Host '{host}' is not in scan scope. Allowed: {', '.join(sorted(self._allowed_hosts))}"

        if self._mode == "block":
            logger.warning(f"Scope guard BLOCKED: {message}")
            return ScopeCheckResult(allowed=False, reason="out_of_scope", message=message)

        logger.info(f"Scope guard WARNING: {message}")
        return ScopeCheckResult(allowed=True, reason="out_of_scope_warned", message=message)

    def check_request_args(
        self, tool_name: str, args: dict[str, Any]
    ) -> ScopeCheckResult:
        """Check if tool arguments are within scope.

        Supports send_request and repeat_request tools.
        """
        if tool_name == "send_request":
            url = args.get("url", "")
            if url:
                return self.check_url(url)

        # repeat_request uses an existing request ID — always in scope
        # (the original request was already captured)
        return ScopeCheckResult(allowed=True, reason="not_applicable")

    @property
    def allowed_hosts(self) -> set[str]:
        return set(self._allowed_hosts)

    @property
    def mode(self) -> str:
        return self._mode

    @staticmethod
    def _extract_host(url: str) -> str | None:
        """Extract and normalize hostname from a URL."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            if host:
                return host.lower().strip()
        except (ValueError, AttributeError):
            pass
        return None


class ScopeCheckResult:
    """Result of a scope check."""

    __slots__ = ("allowed", "reason", "message")

    def __init__(
        self,
        allowed: bool,
        reason: str,
        message: str = "",
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.message = message

    def __bool__(self) -> bool:
        return self.allowed
