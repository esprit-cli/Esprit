"""CloudRuntime — creates sandboxes on the Esprit cloud backend via API."""

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
_CLOUD_SANDBOX_POLL_INTERVAL = 5
_CLOUD_SANDBOX_TIMEOUT = 300


class CloudRuntime(AbstractRuntime):
    """Runtime that executes scans in Esprit cloud sandboxes."""

    def __init__(self, api_url: str, api_token: str) -> None:
        if not api_url or not api_token:
            raise SandboxInitializationError(
                "Cloud runtime requires ESPRIT_API_URL and ESPRIT_API_TOKEN.",
            )
        self._api_url = api_url.rstrip("/")
        self._api_token = api_token
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

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}

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
            async with httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = await client.post(
                    f"{self._api_url}/sandbox",
                    json=payload,
                    headers=self._auth_headers(),
                )
                try:
                    response.raise_for_status()
                    data = response.json()
                except httpx.HTTPStatusError as exc:
                    details = (
                        f"Cloud sandbox API returned HTTP {exc.response.status_code}: "
                        f"{exc.response.text[:500]}"
                    )
                    raise SandboxInitializationError(
                        "Failed to create cloud sandbox.",
                        details,
                    ) from exc
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

        api_url = str(
            data.get("api_url") or f"{self._api_url}/sandbox/{sandbox_id}"
        ).rstrip("/")
        tool_server_port = int(data.get("tool_server_port") or _DEFAULT_CLOUD_TOOL_PORT)
        auth_token = data.get("auth_token") or data.get("sandbox_token") or existing_token

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
            "caido_port": 0,
            "agent_id": agent_id,
        }

    async def get_sandbox_url(self, container_id: str, _port: int) -> str:
        sandbox = self._sandboxes.get(container_id)
        if sandbox and isinstance(sandbox.get("api_url"), str):
            return str(sandbox["api_url"])
        return f"{self._api_url}/sandbox/{container_id}"

    async def destroy_sandbox(self, container_id: str) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = await client.delete(
                    f"{self._api_url}/sandbox/{container_id}",
                    headers=self._auth_headers(),
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
