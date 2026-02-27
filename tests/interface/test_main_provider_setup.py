from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest

interface_main = import_module("esprit.interface.main")


def test_ensure_provider_configured_accepts_public_opencode_model() -> None:
    with (
        patch("esprit.providers.token_store.TokenStore") as token_store_cls,
        patch("esprit.providers.account_pool.get_account_pool") as get_pool,
        patch("esprit.auth.credentials.is_authenticated", return_value=False),
        patch(
            "esprit.interface.main.Config.get",
            side_effect=lambda name: "opencode/minimax-m2.5-free" if name == "esprit_llm" else None,
        ),
    ):
        token_store = MagicMock()
        token_store.has_credentials.return_value = False
        token_store_cls.return_value = token_store

        pool = MagicMock()
        pool.has_accounts.return_value = False
        get_pool.return_value = pool

        assert interface_main.ensure_provider_configured() is True


def test_get_available_models_limits_opencode_to_public_without_api_key() -> None:
    with patch("esprit.providers.token_store.TokenStore") as token_store_cls:
        token_store = MagicMock()
        token_store.has_credentials.return_value = False
        token_store_cls.return_value = token_store

        models = interface_main._get_available_models([("opencode", "Public models (no auth)")])

    model_ids = [model_id for model_id, _ in models]
    assert "opencode/minimax-m2.5-free" in model_ids
    assert "opencode/gpt-5.2-codex" not in model_ids


def test_pull_docker_image_retries_with_amd64_on_arm_manifest_mismatch() -> None:
    client = MagicMock()
    pull_calls: list[dict[str, object]] = []

    def pull_side_effect(_image_name: str, **kwargs: object) -> object:
        pull_calls.append(kwargs)
        if kwargs.get("platform") == "linux/amd64":
            return iter([{"status": "Status: Downloaded newer image"}])
        raise interface_main.DockerException(
            "no matching manifest for linux/arm64/v8 in the manifest list entries"
        )

    client.api.pull.side_effect = pull_side_effect

    console = MagicMock()
    status_ctx = MagicMock()
    status_widget = MagicMock()
    status_ctx.__enter__.return_value = status_widget
    status_ctx.__exit__.return_value = False
    console.status.return_value = status_ctx

    def config_get(name: str) -> str | None:
        if name == "esprit_image":
            return "improdead/esprit-sandbox:latest"
        if name == "esprit_docker_platform":
            return None
        return None

    with (
        patch("esprit.interface.main.Console", return_value=console),
        patch("esprit.interface.main.check_docker_connection", return_value=client),
        patch("esprit.interface.main.image_exists", return_value=False),
        patch("esprit.interface.main.platform.machine", return_value="arm64"),
        patch("esprit.interface.main.Config.get", side_effect=config_get),
        patch.dict(interface_main.os.environ, {}, clear=True),
    ):
        interface_main.pull_docker_image()
        assert interface_main.os.environ["ESPRIT_DOCKER_PLATFORM"] == "linux/amd64"

    assert len(pull_calls) == 2
    assert pull_calls[0].get("platform") is None
    assert pull_calls[1].get("platform") == "linux/amd64"


def test_pull_docker_image_exits_without_fallback_on_non_arm_host() -> None:
    client = MagicMock()
    client.api.pull.side_effect = interface_main.DockerException(
        "no matching manifest for linux/amd64 in the manifest list entries"
    )

    console = MagicMock()
    status_ctx = MagicMock()
    status_widget = MagicMock()
    status_ctx.__enter__.return_value = status_widget
    status_ctx.__exit__.return_value = False
    console.status.return_value = status_ctx

    def config_get(name: str) -> str | None:
        if name == "esprit_image":
            return "improdead/esprit-sandbox:latest"
        if name == "esprit_docker_platform":
            return None
        return None

    with (
        patch("esprit.interface.main.Console", return_value=console),
        patch("esprit.interface.main.check_docker_connection", return_value=client),
        patch("esprit.interface.main.image_exists", return_value=False),
        patch("esprit.interface.main.Config.get", side_effect=config_get),
        patch("esprit.interface.main.sys.exit", side_effect=SystemExit(1)),
    ):
        with pytest.raises(SystemExit):
            interface_main.pull_docker_image()

    client.api.pull.assert_called_once()


def test_pull_docker_image_exits_on_stream_error_payload() -> None:
    client = MagicMock()
    client.api.pull.return_value = iter(
        [
            {"status": "Pulling from improdead/esprit-sandbox"},
            {"error": "unauthorized: authentication required"},
        ]
    )

    console = MagicMock()
    status_ctx = MagicMock()
    status_widget = MagicMock()
    status_ctx.__enter__.return_value = status_widget
    status_ctx.__exit__.return_value = False
    console.status.return_value = status_ctx

    def config_get(name: str) -> str | None:
        if name == "esprit_image":
            return "improdead/esprit-sandbox:latest"
        if name == "esprit_docker_platform":
            return None
        return None

    with (
        patch("esprit.interface.main.Console", return_value=console),
        patch("esprit.interface.main.check_docker_connection", return_value=client),
        patch("esprit.interface.main.image_exists", return_value=False),
        patch("esprit.interface.main.Config.get", side_effect=config_get),
        patch("esprit.interface.main.sys.exit", side_effect=SystemExit(1)),
    ):
        with pytest.raises(SystemExit):
            interface_main.pull_docker_image()

    client.images.get.assert_not_called()


def test_pull_docker_image_exits_when_image_not_present_after_pull() -> None:
    client = MagicMock()
    client.api.pull.return_value = iter([{"status": "Status: Downloaded newer image"}])
    client.images.get.side_effect = interface_main.ImageNotFound("missing")

    console = MagicMock()
    status_ctx = MagicMock()
    status_widget = MagicMock()
    status_ctx.__enter__.return_value = status_widget
    status_ctx.__exit__.return_value = False
    console.status.return_value = status_ctx

    def config_get(name: str) -> str | None:
        if name == "esprit_image":
            return "improdead/esprit-sandbox:latest"
        if name == "esprit_docker_platform":
            return None
        return None

    with (
        patch("esprit.interface.main.Console", return_value=console),
        patch("esprit.interface.main.check_docker_connection", return_value=client),
        patch("esprit.interface.main.image_exists", return_value=False),
        patch("esprit.interface.main.Config.get", side_effect=config_get),
        patch("esprit.interface.main.time.sleep"),
        patch("esprit.interface.main.sys.exit", side_effect=SystemExit(1)),
    ):
        with pytest.raises(SystemExit):
            interface_main.pull_docker_image()

    assert client.images.get.call_count == 3


def test_docker_health_check_allows_missing_local_image_and_defers_pull() -> None:
    client = MagicMock()
    client.ping.return_value = True
    client.info.return_value = {"DockerRootDir": "/tmp"}

    console = MagicMock()

    class _Config:
        @staticmethod
        def get(name: str) -> str | None:
            if name == "esprit_image":
                return "improdead/esprit-sandbox:latest"
            return None

    with (
        patch("docker.from_env", return_value=client),
        patch("esprit.interface.main.image_exists", return_value=False),
        patch("esprit.interface.main.shutil.disk_usage", return_value=MagicMock(free=3 * 1024 * 1024 * 1024)),
    ):
        assert interface_main._docker_health_check(console, _Config) is True

    printed = " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)
    assert "will be pulled in the next step" in printed


def test_docker_health_check_fails_when_image_not_configured() -> None:
    client = MagicMock()
    client.ping.return_value = True
    client.info.return_value = {"DockerRootDir": "/tmp"}

    console = MagicMock()

    class _Config:
        @staticmethod
        def get(name: str) -> str | None:
            if name == "esprit_image":
                return ""
            return None

    with (
        patch("docker.from_env", return_value=client),
        patch("esprit.interface.main.shutil.disk_usage", return_value=MagicMock(free=3 * 1024 * 1024 * 1024)),
    ):
        assert interface_main._docker_health_check(console, _Config) is False
