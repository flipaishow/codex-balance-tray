"""Register the tray application to launch when the current Windows user logs in."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "CodexBalanceTray"


def _winreg_module() -> Any | None:
    """Return ``winreg`` on Windows, while keeping imports portable for tests."""

    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _quoted_argument(path: Path) -> str:
    argument = subprocess.list2cmdline([str(path)])
    return argument if argument.startswith('"') else f'"{argument}"'


def build_startup_command(
    *,
    executable: Path | None = None,
    script: Path | None = None,
    frozen: bool | None = None,
) -> str:
    """Build the command stored in the per-user Windows Run registry key."""

    executable_path = Path(executable or sys.executable).expanduser().resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return _quoted_argument(executable_path)

    script_path = Path(script or sys.argv[0]).expanduser().resolve()
    return f"{_quoted_argument(executable_path)} {_quoted_argument(script_path)}"


def _registry_or_none(registry: Any | None) -> Any | None:
    return registry if registry is not None else _winreg_module()


def registered_startup_command(*, registry: Any | None = None) -> str | None:
    """Read this application's command from the current user's Run key."""

    winreg = _registry_or_none(registry)
    if winreg is None:
        return None

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_READ,
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
    except (FileNotFoundError, OSError):
        return None
    return str(value)


def enable_startup(
    *,
    registry: Any | None = None,
    command: str | None = None,
) -> bool:
    """Write the tray command to the current user's Run key."""

    winreg = _registry_or_none(registry)
    if winreg is None:
        return False

    startup_command = command if command is not None else build_startup_command()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                STARTUP_VALUE_NAME,
                0,
                winreg.REG_SZ,
                startup_command,
            )
    except OSError:
        return False
    return True


def ensure_startup_enabled(
    *,
    registry: Any | None = None,
    command: str | None = None,
) -> bool:
    """Ensure the tray app is registered for login startup.

    Registration is best-effort: a registry failure must not stop the tray
    application from opening and showing the current usage balance.
    """

    startup_command = command if command is not None else build_startup_command()
    if registered_startup_command(registry=registry) == startup_command:
        return True
    return enable_startup(registry=registry, command=startup_command)
