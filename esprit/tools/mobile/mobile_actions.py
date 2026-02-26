from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import Literal

from esprit.tools.registry import register_tool


def _command_available(name: str) -> bool:
    return bool(shutil.which(name))


def _run_command(args: list[str], timeout: int) -> dict[str, object]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Command not found: {args[0]}",
            "command": " ".join(args),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout}s",
            "command": " ".join(args),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    return {
        "success": proc.returncode == 0,
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@register_tool
def mobile_dynamic_status(platform: Literal["android", "ios"] = "android") -> dict[str, object]:
    tools_available = {
        "adb": _command_available("adb"),
        "frida_ps": _command_available("frida-ps"),
        "objection": _command_available("objection"),
    }

    response: dict[str, object] = {
        "platform": platform,
        "tools_available": tools_available,
        "devices": [],
        "notes": [],
    }

    if platform == "android" and tools_available["adb"]:
        adb_result = _run_command(["adb", "devices"], timeout=15)
        response["adb_devices_raw"] = adb_result
        if adb_result.get("success"):
            lines = str(adb_result.get("stdout", "")).splitlines()
            devices: list[dict[str, str]] = []
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    devices.append({
                        "serial": parts[0],
                        "state": parts[1],
                    })
            response["devices"] = devices
    elif platform == "android":
        response["notes"] = [
            "adb not installed or unavailable in sandbox image.",
        ]
    else:
        response["notes"] = [
            "iOS dynamic instrumentation is operator-assisted in v1.",
        ]

    return response


@register_tool
def mobile_adb(command: str, timeout: int = 30) -> dict[str, object]:
    command = command.strip()
    if not command:
        return {
            "success": False,
            "error": "command is required",
            "command": "",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    if "\n" in command or "\r" in command:
        return {
            "success": False,
            "error": "command must be a single line",
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    timeout = max(1, min(timeout, 120))
    args = ["adb", *shlex.split(command)]
    return _run_command(args, timeout=timeout)


@register_tool
def mobile_frida_ps(
    device: Literal["usb", "remote"] = "usb",
    include_apps: bool = True,
    timeout: int = 30,
) -> dict[str, object]:
    timeout = max(1, min(timeout, 120))
    args = ["frida-ps"]
    if device == "usb":
        args.append("-U")
    else:
        args.append("-R")

    if include_apps:
        args.append("-a")

    return _run_command(args, timeout=timeout)
