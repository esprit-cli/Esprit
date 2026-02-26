from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import SandboxInitializationError
from .runtime import AbstractRuntime, SandboxInfo


_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_CLOUD_TOOL_PORT = 443


class CloudRuntime(AbstractRuntime):
    """Runtime that executes scans in Esprit cloud sandboxes."""

    def __init__(self, access_token: str, api_base: str) -> None:
        if not access_token:
            raise SandboxInitializationError(
                "Esprit Cloud authentication required.",
                "Run `esprit login` and try again.",
            )

        self.access_token = access_token
        self.api_base = api_base.rstrip("/")
        self._sandboxes: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _sanitize_local_sources(
        local_sources: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        if not local_sources:
            return []

        sanitized: list[dict[str, str]] = []
        for index, source in enumerate(local_sources, start=1):
            source_path = source.get("source_path")
            if not source_path:
                continue

            sanitized_source_path = Path(source_path).name
            if not sanitized_source_path:
                continue

            target_name = CloudRuntime._sanitize_workspace_subdir(
                source.get("workspace_subdir"), fallback=f"target_{index}"
            )
            sanitized.append(
                {
                    "source_path": sanitized_source_path,
                    "workspace_subdir": target_name,
                }
            )

        return sanitized

    @staticmethod
    def _sanitize_workspace_subdir(raw_subdir: str | None, fallback: str) -> str:
        if not raw_subdir:
            return fallback

        normalized = raw_subdir.replace("\\", "/").strip()
        parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
        if not parts:
            return fallback

        return "/".join(parts)

    @staticmethod
    def _effective_domain(host: str) -> str:
        segments = [segment for segment in host.lower().strip().split(".") if segment]
        if len(segments) >= 2:
            return ".".join(segments[-2:])
        return host.lower().strip()

    @classmethod
    def _is_trusted_runtime_host(cls, runtime_host: str, api_host: str) -> bool:
        runtime = runtime_host.lower().strip()
        base = api_host.lower().strip()
        if not runtime or not base:
            return False

        if runtime == base or runtime.endswith(f".{base}"):
            return True

        return cls._effective_domain(runtime) == cls._effective_domain(base)

    async def create_sandbox(
        self,
        agent_id: str,
        existing_token: str | None = None,
        local_sources: list[dict[str, str]] | None = None,
    ) -> SandboxInfo:
        payload: dict[str, Any] = {"agent_id": agent_id}
        sources_payload = self._sanitize_local_sources(local_sources)
        if sources_payload:
            payload["local_sources"] = sources_payload

        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False) as client:
                response = await client.post(
                    f"{self.api_base}/sandbox/create",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            details = (
                f"Cloud sandbox API returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            )
            raise SandboxInitializationError("Failed to create cloud sandbox.", details) from exc
        except httpx.RequestError as exc:
            raise SandboxInitializationError(
                "Failed to connect to Esprit Cloud sandbox API.",
                str(exc),
            ) from exc

        sandbox_id = str(data.get("sandbox_id") or data.get("workspace_id") or "").strip()
        if not sandbox_id:
            raise SandboxInitializationError(
                "Invalid cloud sandbox response.",
                "Response did not include sandbox_id/workspace_id.",
            )

        api_url = str(data.get("api_url") or f"{self.api_base}/sandbox/{sandbox_id}").rstrip("/")
        runtime_host = urlparse(api_url).hostname or ""
        api_host = urlparse(self.api_base).hostname or ""
        if runtime_host and api_host and not self._is_trusted_runtime_host(runtime_host, api_host):
            raise SandboxInitializationError(
                "Invalid cloud sandbox response.",
                f"untrusted api_url host: {runtime_host}",
            )

        tool_server_port = int(data.get("tool_server_port") or _DEFAULT_CLOUD_TOOL_PORT)
        auth_token = data.get("auth_token") or data.get("sandbox_token") or existing_token
        if not auth_token:
            raise SandboxInitializationError(
                "Invalid cloud sandbox response.",
                "Response did not include sandbox auth token.",
            )

        self._sandboxes[sandbox_id] = {
            "api_url": api_url,
            "auth_token": str(auth_token),
            "tool_server_port": tool_server_port,
            "agent_id": agent_id,
        }

        return {
            "workspace_id": sandbox_id,
            "api_url": api_url,
            "auth_token": str(auth_token),
            "tool_server_port": tool_server_port,
            "agent_id": agent_id,
        }

    async def get_sandbox_url(self, container_id: str, _port: int) -> str:
        sandbox = self._sandboxes.get(container_id)
        if sandbox and isinstance(sandbox.get("api_url"), str):
            return str(sandbox["api_url"])
        return f"{self.api_base}/sandbox/{container_id}"

    async def get_workspace_diffs(self, container_id: str) -> list[dict[str, object]]:
        """Retrieve file edit log from the cloud sandbox tool server."""
        sandbox = self._sandboxes.get(container_id, {})
        api_url = sandbox.get("api_url")
        auth_token = sandbox.get("auth_token")
        if not api_url or not auth_token:
            return []
        try:
            async with httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False
            ) as client:
                resp = await client.get(
                    f"{api_url}/diffs",
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return list(data.get("edits", []))
        except (httpx.RequestError, Exception):  # noqa: BLE001
            pass
        return []

    async def destroy_sandbox(self, container_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False) as client:
                response = await client.delete(
                    f"{self.api_base}/sandbox/{container_id}",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                if response.status_code not in {200, 202, 204, 404}:
                    response.raise_for_status()
        except httpx.RequestError:
            pass
        finally:
            self._sandboxes.pop(container_id, None)

    async def _cleanup_all(self, sandbox_ids: list[str]) -> None:
        for sandbox_id in sandbox_ids:
            await self.destroy_sandbox(sandbox_id)

    def cleanup(self) -> None:
        sandbox_ids = list(self._sandboxes.keys())
        if not sandbox_ids:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._cleanup_all(sandbox_ids))
            return

        for sandbox_id in sandbox_ids:
            loop.create_task(self.destroy_sandbox(sandbox_id))
