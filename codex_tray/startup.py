"""Register the tray application at login on supported desktop platforms."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path, PurePosixPath
import plistlib
import shlex
import subprocess
import sys
import tempfile
from typing import Any


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "CodexBalanceTray"
MACOS_LAUNCH_AGENT_LABEL = "com.flipaishow.codexbalancetray"


def _platform_name(platform: str | None = None) -> str:
    return platform or sys.platform


def _is_windows(platform: str | None = None) -> bool:
    return _platform_name(platform) in {"nt", "win32"}


def _is_macos(platform: str | None = None) -> bool:
    return _platform_name(platform) == "darwin"


_MACOS_COMMON_PATHS = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
)
_MACOS_USER_PATH_SUFFIXES = (
    ".local/bin",
    ".npm-global/bin",
    ".volta/bin",
    ".asdf/shims",
    "bin",
)


def _macos_environment(
    environment: Mapping[str, str] | None = None,
    *,
    home: Path | str | None = None,
) -> dict[str, str]:
    """Build a minimal, non-secret environment for a macOS LaunchAgent."""

    source = environment if environment is not None else os.environ
    home_path = Path(home).expanduser() if home is not None else Path.home()
    path_values = str(source.get("PATH", "")).split(":")
    path_values.extend(_MACOS_COMMON_PATHS)
    path_values.extend(str(home_path / suffix) for suffix in _MACOS_USER_PATH_SUFFIXES)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in path_values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            deduplicated.append(value)

    result = {"PATH": ":".join(deduplicated)}
    codex_home = source.get("CODEX_HOME")
    if codex_home:
        result["CODEX_HOME"] = str(codex_home)
    return result


def _winreg_module() -> Any | None:
    """Return ``winreg`` on Windows, while keeping imports portable for tests."""

    if not _is_windows():
        return None
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _quoted_argument(path: Path) -> str:
    argument = subprocess.list2cmdline([str(path)])
    return argument if argument.startswith('"') else f'"{argument}"'


def _startup_path(
    value: Path | str | None,
    fallback: str,
    *,
    platform: str | None,
) -> str:
    raw_value = os.fspath(value) if value is not None else fallback
    # PurePosixPath lets Windows-hosted tests model macOS paths without
    # converting /Users/... into a Windows drive path. On a real macOS host,
    # resolve the path normally so login startup receives an absolute path.
    if _is_macos(platform) and sys.platform != "darwin":
        return str(PurePosixPath(raw_value))
    return str(Path(raw_value).expanduser().resolve())


def build_startup_arguments(
    *,
    executable: Path | str | None = None,
    script: Path | str | None = None,
    frozen: bool | None = None,
    platform: str | None = None,
) -> list[str]:
    """Build argv for a packaged executable or source checkout."""

    executable_path = _startup_path(executable, sys.executable, platform=platform)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return [executable_path]

    script_path = _startup_path(script, sys.argv[0], platform=platform)
    return [executable_path, script_path]


def build_startup_command(
    *,
    executable: Path | None = None,
    script: Path | None = None,
    frozen: bool | None = None,
    platform: str | None = None,
) -> str:
    """Build the command stored by the platform-specific login mechanism."""

    arguments = build_startup_arguments(
        executable=executable,
        script=script,
        frozen=frozen,
        platform=platform,
    )
    if _is_windows(platform):
        return " ".join(_quoted_argument(Path(argument)) for argument in arguments)
    return shlex.join(arguments)


def default_launch_agent_path(
    *,
    home: Path | str | None = None,
    platform: str | None = None,
) -> Path | None:
    """Return the per-user macOS LaunchAgent path, if running on macOS."""

    if not _is_macos(platform):
        return None
    home_path = Path(home).expanduser() if home is not None else Path.home()
    return home_path / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT_LABEL}.plist"


def _registry_or_none(registry: Any | None) -> Any | None:
    return registry if registry is not None else _winreg_module()


def _load_launch_agent_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return payload if isinstance(payload, dict) else None


def registered_startup_command(
    *,
    registry: Any | None = None,
    launch_agent_path: Path | str | None = None,
    platform: str | None = None,
) -> str | None:
    """Read this application's login command from the current platform."""

    if _is_macos(platform):
        path = Path(launch_agent_path) if launch_agent_path is not None else default_launch_agent_path()
        if path is None:
            return None
        payload = _load_launch_agent_payload(path)
        arguments = payload.get("ProgramArguments") if payload is not None else None
        if not isinstance(arguments, list) or not all(isinstance(argument, str) for argument in arguments):
            return None
        return shlex.join(arguments)

    if not _is_windows(platform):
        return None

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


def registered_startup_environment(
    *,
    launch_agent_path: Path | str | None = None,
    platform: str | None = None,
) -> dict[str, str] | None:
    """Read the safe environment fields stored in a macOS LaunchAgent."""

    if not _is_macos(platform):
        return None
    path = Path(launch_agent_path) if launch_agent_path is not None else default_launch_agent_path()
    if path is None:
        return None
    payload = _load_launch_agent_payload(path)
    environment = payload.get("EnvironmentVariables") if payload is not None else None
    if not isinstance(environment, dict):
        return None
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        return None
    return dict(environment)


def _enable_macos_startup(
    *,
    command: str,
    launch_agent_path: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> bool:
    path = Path(launch_agent_path) if launch_agent_path is not None else default_launch_agent_path()
    if path is None:
        return False

    try:
        arguments = shlex.split(command)
        if not arguments:
            return False
        payload = {
            "Label": MACOS_LAUNCH_AGENT_LABEL,
            "ProgramArguments": arguments,
            "EnvironmentVariables": _macos_environment(environment),
            "RunAtLoad": True,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.name}.",
                dir=path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                plistlib.dump(payload, temporary)
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
    except (OSError, ValueError):
        return False
    return True


def enable_startup(
    *,
    registry: Any | None = None,
    command: str | None = None,
    launch_agent_path: Path | str | None = None,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Write the tray command to the current user's login-startup store."""

    startup_command = command if command is not None else build_startup_command(platform=platform)
    if _is_macos(platform):
        return _enable_macos_startup(
            command=startup_command,
            launch_agent_path=launch_agent_path,
            environment=environment,
        )
    if not _is_windows(platform):
        return False

    winreg = _registry_or_none(registry)
    if winreg is None:
        return False

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
    launch_agent_path: Path | str | None = None,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Ensure login startup is configured without requiring administrator rights."""

    startup_command = command if command is not None else build_startup_command(platform=platform)
    registered_command = registered_startup_command(
        registry=registry,
        launch_agent_path=launch_agent_path,
        platform=platform,
    )
    if registered_command == startup_command:
        if not _is_macos(platform):
            return True
        expected_environment = _macos_environment(environment)
        registered_environment = registered_startup_environment(
            launch_agent_path=launch_agent_path,
            platform=platform,
        )
        if registered_environment is not None and all(
            registered_environment.get(key) == value for key, value in expected_environment.items()
        ):
            return True
    return enable_startup(
        registry=registry,
        command=startup_command,
        launch_agent_path=launch_agent_path,
        platform=platform,
        environment=environment,
    )
