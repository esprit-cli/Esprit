import os

from esprit.config import Config

from .runtime import AbstractRuntime


class SandboxInitializationError(Exception):
    """Raised when sandbox initialization fails (e.g., Docker issues)."""

    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


_global_runtime: AbstractRuntime | None = None


def get_runtime() -> AbstractRuntime:
    global _global_runtime  # noqa: PLW0603

    runtime_backend = Config.get("esprit_runtime_backend")

    if runtime_backend == "cloud":
        from esprit.auth.credentials import get_auth_token

        from .cloud_runtime import CloudRuntime

        auth_token = get_auth_token()
        if not auth_token:
            raise SandboxInitializationError(
                "Esprit Cloud authentication required.",
                "Run `esprit login` and try again.",
            )

        api_base = os.getenv("ESPRIT_API_URL", "https://esprit.dev/api/v1")
        if _global_runtime is None or not isinstance(_global_runtime, CloudRuntime):
            _global_runtime = CloudRuntime(access_token=auth_token, api_base=api_base)
        return _global_runtime

    if runtime_backend == "docker":
        from .docker_runtime import DockerRuntime

        if _global_runtime is None or not isinstance(_global_runtime, DockerRuntime):
            _global_runtime = DockerRuntime()
        return _global_runtime

    raise ValueError(f"Unsupported runtime backend: {runtime_backend}.")


def cleanup_runtime() -> None:
    global _global_runtime  # noqa: PLW0603

    if _global_runtime is not None:
        _global_runtime.cleanup()
        _global_runtime = None


__all__ = ["AbstractRuntime", "SandboxInitializationError", "cleanup_runtime", "get_runtime"]
