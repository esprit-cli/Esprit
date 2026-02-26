from abc import ABC, abstractmethod
from typing import TypedDict


class SandboxInfo(TypedDict):
    workspace_id: str
    api_url: str
    auth_token: str | None
    tool_server_port: int
    agent_id: str


class AbstractRuntime(ABC):
    @abstractmethod
    async def create_sandbox(
        self,
        agent_id: str,
        existing_token: str | None = None,
        local_sources: list[dict[str, str]] | None = None,
        artifacts: list[dict[str, str]] | None = None,
    ) -> SandboxInfo:
        raise NotImplementedError

    @abstractmethod
    async def get_sandbox_url(self, container_id: str, port: int) -> str:
        raise NotImplementedError

    @abstractmethod
    async def destroy_sandbox(self, container_id: str) -> None:
        raise NotImplementedError

    async def get_workspace_diffs(self, container_id: str) -> list[dict[str, object]]:
        """Retrieve file edits from the sandbox before it is destroyed.

        Returns a list of edit records (command, path, old_str, new_str, etc.).
        Default implementation returns an empty list for runtimes that don't
        support diff extraction.
        """
        return []

    def cleanup(self) -> None:
        raise NotImplementedError
