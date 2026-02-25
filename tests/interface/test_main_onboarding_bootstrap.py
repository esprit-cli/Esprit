import sys
import types
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch


def _stub_interface_deps() -> None:
    launchpad_module = types.ModuleType("esprit.interface.launchpad")
    launchpad_module.LaunchpadResult = object

    async def _run_launchpad():  # pragma: no cover - import shim only
        return None

    launchpad_module.run_launchpad = _run_launchpad
    sys.modules["esprit.interface.launchpad"] = launchpad_module

    onboarding_module = types.ModuleType("esprit.interface.onboarding")

    async def _run_onboarding():  # pragma: no cover - import shim only
        return None

    onboarding_module.run_onboarding = _run_onboarding
    sys.modules["esprit.interface.onboarding"] = onboarding_module

    tui_module = types.ModuleType("esprit.interface.tui")

    async def _run_tui(*_args, **_kwargs):  # pragma: no cover - import shim only
        return None

    tui_module.run_tui = _run_tui
    sys.modules["esprit.interface.tui"] = tui_module


_stub_interface_deps()
interface_main = import_module("esprit.interface.main")


def test_should_bypass_onboarding_for_help_version_and_uninstall() -> None:
    assert interface_main._should_bypass_onboarding(["--help"]) is True
    assert interface_main._should_bypass_onboarding(["provider", "--help"]) is True
    assert interface_main._should_bypass_onboarding(["-v"]) is True
    assert interface_main._should_bypass_onboarding(["uninstall"]) is True
    assert interface_main._should_bypass_onboarding(["scan", "https://example.com"]) is False


def test_run_first_time_onboarding_skips_when_not_required() -> None:
    with (
        patch("esprit.interface.main.Config.is_onboarding_required", return_value=False),
        patch("esprit.interface.main.asyncio.run") as run_mock,
    ):
        assert interface_main._run_first_time_onboarding(["scan", "https://example.com"]) is True
        run_mock.assert_not_called()


def test_run_first_time_onboarding_marks_completed() -> None:
    with (
        patch("esprit.interface.main.Config.is_onboarding_required", return_value=True),
        patch("esprit.interface.main.run_onboarding", new=lambda: "ignored"),
        patch(
            "esprit.interface.main.asyncio.run",
            return_value=SimpleNamespace(action="completed"),
        ),
        patch("esprit.interface.main.Config.mark_onboarding_completed") as mark_completed,
        patch("esprit.interface.main.Config.mark_onboarding_skipped") as mark_skipped,
    ):
        assert interface_main._run_first_time_onboarding(["scan", "https://example.com"]) is True
        mark_completed.assert_called_once_with(version=interface_main._ONBOARDING_VERSION)
        mark_skipped.assert_not_called()


def test_run_first_time_onboarding_marks_skipped() -> None:
    with (
        patch("esprit.interface.main.Config.is_onboarding_required", return_value=True),
        patch("esprit.interface.main.run_onboarding", new=lambda: "ignored"),
        patch(
            "esprit.interface.main.asyncio.run",
            return_value=SimpleNamespace(action="skipped"),
        ),
        patch("esprit.interface.main.Config.mark_onboarding_completed") as mark_completed,
        patch("esprit.interface.main.Config.mark_onboarding_skipped") as mark_skipped,
    ):
        assert interface_main._run_first_time_onboarding(["provider", "status"]) is True
        mark_skipped.assert_called_once_with(version=interface_main._ONBOARDING_VERSION)
        mark_completed.assert_not_called()


def test_run_first_time_onboarding_aborts_on_exit() -> None:
    with (
        patch("esprit.interface.main.Config.is_onboarding_required", return_value=True),
        patch("esprit.interface.main.run_onboarding", new=lambda: "ignored"),
        patch("esprit.interface.main.asyncio.run", return_value=SimpleNamespace(action="exit")),
        patch("esprit.interface.main.Config.mark_onboarding_completed") as mark_completed,
        patch("esprit.interface.main.Config.mark_onboarding_skipped") as mark_skipped,
    ):
        assert interface_main._run_first_time_onboarding(["scan", "https://example.com"]) is False
        mark_completed.assert_not_called()
        mark_skipped.assert_not_called()


def test_run_first_time_onboarding_bypasses_for_version_command() -> None:
    with (
        patch("esprit.interface.main.Config.is_onboarding_required") as required_mock,
        patch("esprit.interface.main.asyncio.run") as run_mock,
    ):
        assert interface_main._run_first_time_onboarding(["--version"]) is True
        required_mock.assert_not_called()
        run_mock.assert_not_called()
