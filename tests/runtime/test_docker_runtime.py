"""Tests for esprit.runtime.docker_runtime module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from esprit.runtime import SandboxInitializationError
from esprit.runtime.docker_runtime import DockerRuntime


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client."""
    with patch("esprit.runtime.docker_runtime.docker") as mock_docker:
        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client
        yield mock_client


@pytest.fixture
def runtime(mock_docker_client):
    """Create a DockerRuntime instance with a mocked Docker client."""
    rt = DockerRuntime()
    return rt


class TestWaitForToolServer:
    """Tests for the _wait_for_tool_server method."""

    def test_healthy_server_returns_immediately(self, runtime):
        """Test that a healthy server is detected on first attempt."""
        runtime._tool_server_port = 12345
        runtime._scan_container = MagicMock()
        runtime._scan_container.status = "running"

        with patch("esprit.runtime.docker_runtime.time.sleep"):
            with patch("esprit.runtime.docker_runtime.httpx.Client") as mock_client_cls:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "healthy"}
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                # Should not raise
                runtime._wait_for_tool_server(max_retries=3, timeout=1)

    def test_dead_container_raises_with_logs(self, runtime):
        """Test that a dead container raises immediately with container logs."""
        runtime._tool_server_port = 12345
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 1}}
        mock_container.logs.return_value = b"ERROR: Caido process died\n=== Caido log ===\nsegfault"
        runtime._scan_container = mock_container

        with patch("esprit.runtime.docker_runtime.time.sleep"):
            with patch("esprit.runtime.docker_runtime.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.side_effect = httpx.ConnectError("refused")
                mock_client_cls.return_value = mock_client

                with pytest.raises(SandboxInitializationError) as exc_info:
                    runtime._wait_for_tool_server(max_retries=3, timeout=1)

                assert "exited with code 1" in exc_info.value.details
                assert "Caido process died" in exc_info.value.details

    def test_removed_container_raises(self, runtime):
        """Test that a removed container raises with a clear message."""
        from docker.errors import NotFound

        runtime._tool_server_port = 12345
        mock_container = MagicMock()
        mock_container.reload.side_effect = NotFound("gone")
        runtime._scan_container = mock_container

        with patch("esprit.runtime.docker_runtime.time.sleep"):
            with pytest.raises(SandboxInitializationError) as exc_info:
                runtime._wait_for_tool_server(max_retries=3, timeout=1)

            assert "removed during initialization" in exc_info.value.details

    def test_timeout_includes_container_logs(self, runtime):
        """Test that timeout error includes container logs for diagnostics."""
        runtime._tool_server_port = 12345
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.logs.return_value = b"Starting tool server...\nWaiting for Caido..."
        runtime._scan_container = mock_container

        with patch("esprit.runtime.docker_runtime.time.sleep"):
            with patch("esprit.runtime.docker_runtime.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.side_effect = httpx.ConnectError("refused")
                mock_client_cls.return_value = mock_client

                with pytest.raises(SandboxInitializationError) as exc_info:
                    runtime._wait_for_tool_server(max_retries=2, timeout=1)

                assert "timed out after 2 attempts" in exc_info.value.details
                assert "Container logs:" in exc_info.value.details
                assert "Starting tool server" in exc_info.value.details


class TestGetContainerLogs:
    """Tests for the _get_container_logs method."""

    def test_returns_logs_from_container(self, runtime):
        """Test that logs are returned from the container."""
        mock_container = MagicMock()
        mock_container.logs.return_value = b"some log output"
        runtime._scan_container = mock_container

        result = runtime._get_container_logs()
        assert result == "some log output"

    def test_returns_message_when_no_container(self, runtime):
        """Test that a message is returned when there's no container."""
        runtime._scan_container = None
        result = runtime._get_container_logs()
        assert result == "(no container)"

    def test_returns_message_on_error(self, runtime):
        """Test that a message is returned when logs can't be retrieved."""
        mock_container = MagicMock()
        mock_container.logs.side_effect = Exception("docker error")
        runtime._scan_container = mock_container

        result = runtime._get_container_logs()
        assert result == "(unable to retrieve logs)"


class TestDockerfileMobileTooling:
    def test_includes_mobile_static_tools(self):
        dockerfile = Path("containers/Dockerfile").read_text(encoding="utf-8")
        assert "apktool" in dockerfile
        assert "jadx" in dockerfile
        assert "aapt" in dockerfile
        assert "binwalk" in dockerfile
        assert "mitmproxy" in dockerfile
        assert "pipx install yq" in dockerfile

    def test_has_optional_android_dynamic_tool_flag(self):
        dockerfile = Path("containers/Dockerfile").read_text(encoding="utf-8")
        assert "ARG INSTALL_ANDROID_DYNAMIC_TOOLS=false" in dockerfile
        assert "frida-tools" in dockerfile
        assert "objection" in dockerfile
