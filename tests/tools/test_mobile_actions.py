"""Tests for mobile dynamic helper tools."""

from types import SimpleNamespace
from typing import Any

import esprit.tools.mobile.mobile_actions as mobile_actions


class TestMobileDynamicStatus:
    def test_parses_android_devices(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(mobile_actions, "_command_available", lambda _name: True)
        monkeypatch.setattr(
            mobile_actions,
            "_run_command",
            lambda _args, timeout: {
                "success": True,
                "command": "adb devices",
                "exit_code": 0,
                "stdout": "List of devices attached\nemulator-5554\tdevice\n",
                "stderr": "",
            },
        )

        result = mobile_actions.mobile_dynamic_status("android")
        assert result["tools_available"]["adb"] is True
        devices = result["devices"]
        assert isinstance(devices, list)
        assert devices[0]["serial"] == "emulator-5554"
        assert devices[0]["state"] == "device"

    def test_ios_returns_operator_assisted_note(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(mobile_actions, "_command_available", lambda _name: False)
        result = mobile_actions.mobile_dynamic_status("ios")
        notes = result["notes"]
        assert isinstance(notes, list)
        assert "operator-assisted" in notes[0]


class TestMobileAdb:
    def test_rejects_multiline(self) -> None:
        result = mobile_actions.mobile_adb("devices\nshell id")
        assert result["success"] is False
        assert "single line" in str(result["error"])

    def test_runs_adb_command(self, monkeypatch: Any) -> None:
        def _fake_run(args: list[str], capture_output: bool, text: bool, timeout: int, check: bool) -> Any:
            assert args[:2] == ["adb", "devices"]
            assert timeout == 10
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(mobile_actions.subprocess, "run", _fake_run)
        result = mobile_actions.mobile_adb("devices", timeout=10)
        assert result["success"] is True
        assert result["exit_code"] == 0
        assert result["stdout"] == "ok"


class TestMobileFridaPs:
    def test_uses_usb_and_include_apps_by_default(self, monkeypatch: Any) -> None:
        calls: list[list[str]] = []

        def _fake_run_command(args: list[str], timeout: int) -> dict[str, object]:
            calls.append(args)
            return {"success": True, "command": " ".join(args), "exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(mobile_actions, "_run_command", _fake_run_command)
        result = mobile_actions.mobile_frida_ps()
        assert result["success"] is True
        assert calls[0] == ["frida-ps", "-U", "-a"]

    def test_uses_remote_without_apps(self, monkeypatch: Any) -> None:
        calls: list[list[str]] = []

        def _fake_run_command(args: list[str], timeout: int) -> dict[str, object]:
            calls.append(args)
            return {"success": True, "command": " ".join(args), "exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(mobile_actions, "_run_command", _fake_run_command)
        mobile_actions.mobile_frida_ps(device="remote", include_apps=False)
        assert calls[0] == ["frida-ps", "-R"]
