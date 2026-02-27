from __future__ import annotations

import asyncio
import os
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

            target_name = source.get("workspace_subdir") or Path(source_path).name or f"target_{index}"
            sanitized.append(
                {
                    "source_path": source_path,
                    "workspace_subdir": target_name,
                }
            )

        return sanitized

    @staticmethod
    def _build_modern_sandbox_payload(
        agent_id: str,
        sources_payload: list[dict[str, str]],
    ) -> dict[str, Any]:
        scan_mode = str(os.getenv("ESPRIT_SCAN_MODE", "quick")).strip().lower()
        if scan_mode not in {"quick", "deep", "compliance"}:
            scan_mode = "quick"

        default_target = "https://example.com"
        target = default_target
        target_type = "url"
        if sources_payload:
            target = sources_payload[0].get("workspace_subdir") or default_target
            target_type = "local_upload"

        return {
            "scan_id": f"cli-{agent_id}",
            "target": target,
            "target_type": target_type,
            "scan_type": scan_mode,
        }

    async def _create_sandbox_request(
        self,
        client: httpx.AsyncClient,
        agent_id: str,
        sources_payload: list[dict[str, str]],
    ) -> tuple[dict[str, Any], bool]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        legacy_payload: dict[str, Any] = {"agent_id": agent_id}
        if sources_payload:
            legacy_payload["local_sources"] = sources_payload

        legacy_response = await client.post(
            f"{self.api_base}/sandbox/create",
            json=legacy_payload,
            headers=headers,
        )
        try:
            legacy_response.raise_for_status()
            return legacy_response.json(), False
        except httpx.HTTPStatusError as legacy_exc:
            # New backend shape uses POST /sandbox and no longer supports /sandbox/create.
            if legacy_exc.response.status_code not in {404, 405}:
                raise

        modern_payload = self._build_modern_sandbox_payload(agent_id, sources_payload)
        modern_response = await client.post(
            f"{self.api_base}/sandbox",
            json=modern_payload,
            headers=headers,
        )
        modern_response.raise_for_status()
        return modern_response.json(), True

    async def _poll_tool_server_url(self, sandbox_id: str) -> str | None:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        status_url = f"{self.api_base}/sandbox/{sandbox_id}"
        timeout = httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS, connect=10)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            for _ in range(20):
                response = await client.get(status_url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    tool_server_url = str(data.get("tool_server_url") or "").strip()
                    if tool_server_url:
                        return tool_server_url.rstrip("/")
                elif response.status_code in {404, 410}:
                    return None
                await asyncio.sleep(1)
        return None

    async def create_sandbox(
        self,
        agent_id: str,
        existing_token: str | None = None,
        local_sources: list[dict[str, str]] | None = None,
    ) -> SandboxInfo:
        sources_payload = self._sanitize_local_sources(local_sources)
        used_modern_endpoint = False

        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False) as client:
                data, used_modern_endpoint = await self._create_sandbox_request(
                    client=client,
                    agent_id=agent_id,
                    sources_payload=sources_payload,
                )
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

        api_url = str(data.get("api_url") or data.get("tool_server_url") or "").rstrip("/")
        if used_modern_endpoint and not api_url:
            api_url = (await self._poll_tool_server_url(sandbox_id)) or ""
        if not api_url:
            api_url = f"{self.api_base}/sandbox/{sandbox_id}"

        tool_server_port = int(data.get("tool_server_port") or _DEFAULT_CLOUD_TOOL_PORT)
        parsed_port = urlparse(api_url).port
        if parsed_port:
            tool_server_port = int(parsed_port)

        auth_token = data.get("auth_token") or data.get("sandbox_token") or existing_token or self.access_token

        self._sandboxes[sandbox_id] = {
            "api_url": api_url,
            "auth_token": str(auth_token) if auth_token else None,
            "tool_server_port": tool_server_port,
            "agent_id": agent_id,
        }

        return {
            "workspace_id": sandbox_id,
            "api_url": api_url,
            "auth_token": str(auth_token) if auth_token else None,
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
