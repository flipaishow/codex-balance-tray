"""Safe, source-independent Codex quota providers.

The tray UI should consume :class:`BalanceResult` only.  This module keeps
Codex's JSON-RPC/HTTP details, retry policy, and credential boundaries out of
presentation code.  The preferred source is the official ``codex app-server``
(which owns OAuth and Windows credential-store access); the HTTP adapter is an
explicitly supplied fallback for compatible environments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Literal
from urllib.parse import urlsplit

import requests


Status = Literal[
    "ok",
    "loading",
    "stale",
    "auth_required",
    "unsupported_auth",
    "cli_unavailable",
    "network_error",
    "rate_limited",
    "unsupported_route",
    "schema_changed",
    "service_error",
    "unavailable",
]
Source = Literal["app_server", "chatgpt_backend", "none", "mock"]
_TRUSTED_HTTP_HOSTS = {"chatgpt.com", "www.chatgpt.com", "chat.openai.com", "api.openai.com"}
_CHATGPT_HTTP_HOSTS = {"chatgpt.com", "www.chatgpt.com"}

DEFAULT_APP_SERVER_TIMEOUT_SECONDS = 20.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_LINE_BYTES = 1_000_000
DEFAULT_MAX_RETRY_AFTER_SECONDS = 3_600


def _macos_path_entries(home: str | Path | None = None) -> list[str]:
    home_path = Path(home).expanduser() if home is not None else Path.home()
    return [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        str(home_path / ".local" / "bin"),
        str(home_path / ".npm-global" / "bin"),
        str(home_path / ".volta" / "bin"),
        str(home_path / ".asdf" / "shims"),
        str(home_path / "bin"),
    ]


def find_codex_executable(
    executable_name: str = "codex",
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    local_app_data: str | Path | None = None,
    home: str | Path | None = None,
) -> str | None:
    """Find Codex on PATH or in common desktop CLI installation locations."""

    executable = which(executable_name)
    if executable:
        return executable

    platform_name = platform or sys.platform
    if platform_name == "darwin":
        candidates = [Path(directory) / executable_name for directory in _macos_path_entries(home)]
        for candidate in candidates:
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
        return None

    if platform_name not in {"nt", "win32"}:
        return None

    if local_app_data is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return None
    bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    windows_name = executable_name if executable_name.lower().endswith(".exe") else f"{executable_name}.exe"
    candidates = [bin_root / windows_name]
    candidates.extend(sorted(bin_root.glob(f"*/{windows_name}")))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def _subprocess_environment(
    executable: str,
    *,
    platform: str | None = None,
    home: str | Path | None = None,
) -> dict[str, str]:
    """Preserve Codex's environment and add paths needed by macOS launchd."""

    environment = dict(os.environ)
    if (platform or sys.platform) != "darwin":
        return environment

    path_values = [str(Path(executable).parent), *_macos_path_entries(home)]
    path_values.extend(str(environment.get("PATH", "")).split(":"))
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in path_values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            deduplicated.append(value)
    environment["PATH"] = ":".join(deduplicated)
    return environment


@dataclass(frozen=True)
class QuotaWindow:
    """A validated Codex quota window."""

    kind: Literal["primary", "secondary"]
    used_percent: int | None
    remaining_percent: int | None
    window_seconds: int | None
    resets_at: int | None


@dataclass(frozen=True)
class BalanceResult:
    """The stable, display-safe result exposed to the rest of the tray.

    ``balance`` is the remaining value in ``unit``.  For the current Codex
    quota APIs that unit is ``%``.  Unknown values stay ``None``; a malformed
    response is never interpreted as 0% or 100%.
    """

    status: Status = "schema_changed"
    source: Source = "none"
    balance: int | float | str | None = None
    unit: str = "%"
    # ``remaining`` and ``error`` are the concise contract names used by the
    # UI layer. The explicit ``*_percent``/``*_message`` fields remain for
    # compatibility with the transport adapters and the original tray code.
    remaining: int | float | str | None = None
    used_percent: int | None = None
    remaining_percent: int | None = None
    reset_at: int | None = None
    reset_after_seconds: int | None = None
    window_seconds: int | None = None
    retrieved_at: str | datetime | None = None
    last_success_at: str | datetime | None = None
    plan_type: str | None = None
    primary: QuotaWindow | None = None
    secondary: QuotaWindow | None = None
    credits_balance: str | None = None
    has_credits: bool | None = None
    unlimited: bool | None = None
    rate_limit_reached_type: str | None = None
    spend_control_reached: bool | None = None
    token_usage: Mapping[str, object] | None = None
    retry_after_seconds: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        remaining = self.remaining
        if remaining is None:
            remaining = self.remaining_percent if self.remaining_percent is not None else self.balance
            if remaining is not None:
                object.__setattr__(self, "remaining", remaining)
        if self.balance is None and remaining is not None:
            object.__setattr__(self, "balance", remaining)
        if self.remaining_percent is None and self.unit == "%" and isinstance(remaining, int):
            object.__setattr__(self, "remaining_percent", remaining)
        if self.error_message is None and self.error is not None:
            object.__setattr__(self, "error_message", self.error)
        if self.error is None and self.error_message is not None:
            object.__setattr__(self, "error", self.error_message)

    @property
    def is_success(self) -> bool:
        return self.status == "ok"

    @property
    def is_stale(self) -> bool:
        return self.status == "stale"

    @property
    def reset_time(self) -> int | None:
        """Compatibility alias for the server's Unix reset timestamp."""

        return self.reset_at


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for transient provider failures."""

    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (60.0, 120.0, 300.0, 600.0)
    jitter_ratio: float = 0.1
    # Retained for configuration compatibility; it must not cap a server
    # supplied Retry-After minimum.
    max_retry_after_seconds: int = DEFAULT_MAX_RETRY_AFTER_SECONDS

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.jitter_ratio < 0:
            raise ValueError("jitter_ratio must not be negative")
        if self.max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds must not be negative")

    def delay(
        self,
        attempt: int,
        *,
        retry_after_seconds: int | None = None,
        random_value: Callable[[float, float], float] = random.uniform,
    ) -> float:
        """Return the delay after the 1-based failed ``attempt``."""

        if retry_after_seconds is not None:
            # Retry-After is a server-provided minimum. Never apply the
            # client-side maximum or jitter to it; doing so can violate the
            # server's rate-limit guidance.
            return float(max(0, retry_after_seconds))
        else:
            index = max(0, min(attempt - 1, len(self.backoff_seconds) - 1))
            base = self.backoff_seconds[index] if self.backoff_seconds else 0.0
        if not base or not self.jitter_ratio:
            return float(base)
        spread = base * min(self.jitter_ratio, 1.0)
        return max(0.0, random_value(base - spread, base + spread))


class BalanceProviderError(RuntimeError):
    """Internal exception for provider setup failures.

    Public fetch methods convert this into a ``BalanceResult``.  The exception
    deliberately stores only a fixed, safe message and never an upstream body.
    """

    def __init__(self, status: Status, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message


_SAFE_PROVIDER_STATUSES: dict[str, str] = {
    "auth_required": "無法取得 Codex 登入資料，請先在 Codex 重新登入",
    "unsupported_auth": "HTTP fallback 未設定安全的 Codex 認證來源",
    "cli_unavailable": "Codex app-server 無法使用",
    "network_error": "網路暫時無法取得 Codex 額度",
    "rate_limited": "Codex 服務要求稍後重試",
    "unsupported_route": "Codex 額度 endpoint 不相容",
    "schema_changed": "Codex 回應格式已變更，暫時無法解析額度資料",
    "service_error": "Codex 服務暫時無法提供額度資料",
}
_SAFE_PROVIDER_ERROR_CODES = {
    "AUTH_REQUIRED",
    "UNSUPPORTED_AUTH",
    "CLI_NOT_FOUND",
    "CLI_START_FAILED",
    "NETWORK_ERROR",
    "TIMEOUT",
    "CLI_IO_ERROR",
    "HTTP_ERROR",
    "APP_SERVER_ERROR",
    "NO_SNAPSHOT",
    "MISSING_RESPONSE",
    "SCHEMA_CHANGED",
    "PROVIDER_ERROR",
    "INVALID_PROVIDER_RESULT",
    "SERVICE_UNAVAILABLE",
}


def _safe_provider_error(exc: BalanceProviderError) -> BalanceProviderError:
    """Keep only a known classification when an adapter crosses a trust boundary."""

    raw_status = exc.status
    status = raw_status if isinstance(raw_status, str) and raw_status in _SAFE_PROVIDER_STATUSES else "service_error"
    raw_code = exc.error_code
    code = raw_code if isinstance(raw_code, str) and raw_code in _SAFE_PROVIDER_ERROR_CODES else None
    if isinstance(raw_code, str) and raw_code.startswith("HTTP_"):
        suffix = raw_code.removeprefix("HTTP_")
        if len(suffix) == 3 and suffix.isdigit():
            code = raw_code
    if code is None:
        code = {
            "auth_required": "AUTH_REQUIRED",
            "unsupported_auth": "UNSUPPORTED_AUTH",
            "schema_changed": "SCHEMA_CHANGED",
            "network_error": "NETWORK_ERROR",
            "rate_limited": "RATE_LIMITED",
            "unsupported_route": "UNSUPPORTED_ROUTE",
            "cli_unavailable": "CLI_UNAVAILABLE",
            "service_error": "PROVIDER_ERROR",
        }[status]
    return BalanceProviderError(status, code, _SAFE_PROVIDER_STATUSES[status])


class _RetryableResult(Exception):
    def __init__(self, result: BalanceResult) -> None:
        super().__init__(result.error_message or result.status)
        self.result = result


class _NonRetryableResult(Exception):
    def __init__(self, result: BalanceResult) -> None:
        super().__init__(result.error_message or result.status)
        self.result = result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _epoch_seconds(now: Callable[[], datetime]) -> int:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _result(
    *,
    status: Status,
    source: Source,
    now: Callable[[], datetime],
    error_code: str,
    error_message: str,
    retry_after_seconds: int | None = None,
) -> BalanceResult:
    return BalanceResult(
        status=status,
        source=source,
        retrieved_at=_timestamp(now),
        error_code=error_code,
        error_message=error_message,
        retry_after_seconds=retry_after_seconds,
    )


def _strict_int(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _optional_int(value: Any, *, minimum: int | None = None) -> tuple[int | None, bool]:
    """Return (value, valid), distinguishing a missing field from bad data."""

    if value is None:
        return None, True
    parsed = _strict_int(value, minimum=minimum)
    return parsed, parsed is not None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _failure_from_result(result: BalanceResult) -> BalanceResult:
    return result


def _window_from_app_server(
    value: Any,
    *,
    kind: Literal["primary", "secondary"],
    now: Callable[[], datetime],
) -> QuotaWindow | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BalanceProviderError(
            "schema_changed",
            "SCHEMA_CHANGED",
            "Codex 回應格式已變更，暫時無法解析額度資料",
        )

    used = _strict_int(value.get("usedPercent"), minimum=0, maximum=100)
    if used is None:
        raise BalanceProviderError(
            "schema_changed",
            "SCHEMA_CHANGED",
            "Codex 回應格式已變更，暫時無法解析額度資料",
        )
    window_minutes, valid_minutes = _optional_int(value.get("windowDurationMins"), minimum=0)
    resets_at, valid_reset = _optional_int(value.get("resetsAt"), minimum=0)
    if not valid_minutes or not valid_reset:
        raise BalanceProviderError(
            "schema_changed",
            "SCHEMA_CHANGED",
            "Codex 回應格式已變更，暫時無法解析額度資料",
        )
    return QuotaWindow(
        kind=kind,
        used_percent=used,
        remaining_percent=100 - used,
        window_seconds=None if window_minutes is None else window_minutes * 60,
        resets_at=resets_at,
    )


def parse_app_server_rate_limits(
    payload: Mapping[str, Any],
    *,
    now: Callable[[], datetime] = _utc_now,
    source: Source = "app_server",
) -> BalanceResult:
    """Parse an ``account/rateLimits/read`` JSON-RPC response."""

    try:
        result = payload.get("result", payload) if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )

        by_limit = result.get("rateLimitsByLimitId")
        selected: Any = None
        if isinstance(by_limit, Mapping):
            selected = by_limit.get("codex")
        if selected is None:
            selected = result.get("rateLimits")
        if not isinstance(selected, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        compatibility = result.get("rateLimits")
        if not isinstance(compatibility, Mapping):
            compatibility = {}

        def field(name: str) -> Any:
            return selected[name] if name in selected else compatibility.get(name)

        primary = _window_from_app_server(selected.get("primary"), kind="primary", now=now)
        if primary is None:
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        secondary = _window_from_app_server(field("secondary"), kind="secondary", now=now)

        credits = field("credits")
        if credits is not None and not isinstance(credits, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        credits = credits if isinstance(credits, Mapping) else {}
        balance = credits.get("balance")
        if balance is not None and not isinstance(balance, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        has_credits = credits.get("hasCredits")
        unlimited = credits.get("unlimited")
        if has_credits is not None and not isinstance(has_credits, bool):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        if unlimited is not None and not isinstance(unlimited, bool):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )

        reset_after = None
        if primary.resets_at is not None:
            reset_after = max(0, primary.resets_at - _epoch_seconds(now))
        plan_type = field("planType")
        if plan_type is not None and not isinstance(plan_type, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        reached_type = field("rateLimitReachedType")
        if reached_type is not None and not isinstance(reached_type, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        spend_control = field("spendControlReached")
        if spend_control is not None and not isinstance(spend_control, bool):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )

        retrieved_at = _timestamp(now)
        return BalanceResult(
            status="ok",
            source=source,
            balance=primary.remaining_percent,
            unit="%",
            used_percent=primary.used_percent,
            remaining_percent=primary.remaining_percent,
            reset_at=primary.resets_at,
            reset_after_seconds=reset_after,
            window_seconds=primary.window_seconds,
            retrieved_at=retrieved_at,
            last_success_at=retrieved_at,
            plan_type=plan_type,
            primary=primary,
            secondary=secondary,
            credits_balance=balance,
            has_credits=has_credits,
            unlimited=unlimited,
            rate_limit_reached_type=reached_type,
            spend_control_reached=spend_control,
        )
    except BalanceProviderError as exc:
        return _result(
            status=exc.status,
            source=source,
            now=now,
            error_code=exc.error_code,
            error_message=exc.message,
        )


def parse_http_usage_payload(
    payload: Mapping[str, Any],
    *,
    now: Callable[[], datetime] = _utc_now,
    source: Source = "chatgpt_backend",
) -> BalanceResult:
    """Parse the snake_case payload returned by the private HTTP fallback."""

    try:
        if not isinstance(payload, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        rate_limit = payload.get("rate_limit")
        if not isinstance(rate_limit, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        primary_data = rate_limit.get("primary_window")
        if not isinstance(primary_data, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        used = _strict_int(primary_data.get("used_percent"), minimum=0, maximum=100)
        if used is None:
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )

        def parse_window(value: Any, kind: Literal["primary", "secondary"]) -> QuotaWindow | None:
            if value is None:
                return None
            if not isinstance(value, Mapping):
                raise BalanceProviderError(
                    "schema_changed",
                    "SCHEMA_CHANGED",
                    "Codex 回應格式已變更，暫時無法解析額度資料",
                )
            used_value = _strict_int(value.get("used_percent"), minimum=0, maximum=100)
            if used_value is None:
                raise BalanceProviderError(
                    "schema_changed",
                    "SCHEMA_CHANGED",
                    "Codex 回應格式已變更，暫時無法解析額度資料",
                )
            window_seconds, valid_window = _optional_int(value.get("limit_window_seconds"), minimum=0)
            reset_after, valid_after = _optional_int(value.get("reset_after_seconds"), minimum=0)
            reset_at, valid_at = _optional_int(value.get("reset_at"), minimum=0)
            if not valid_window or not valid_after or not valid_at:
                raise BalanceProviderError(
                    "schema_changed",
                    "SCHEMA_CHANGED",
                    "Codex 回應格式已變更，暫時無法解析額度資料",
                )
            return QuotaWindow(
                kind=kind,
                used_percent=used_value,
                remaining_percent=100 - used_value,
                window_seconds=window_seconds,
                resets_at=reset_at,
            )

        primary = parse_window(primary_data, "primary")
        assert primary is not None
        secondary = parse_window(rate_limit.get("secondary_window"), "secondary")
        credits_data = payload.get("credits")
        if credits_data is not None and not isinstance(credits_data, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        credits = credits_data if isinstance(credits_data, Mapping) else {}
        balance = credits.get("balance")
        if balance is not None and not isinstance(balance, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        for key in ("has_credits", "unlimited"):
            if credits.get(key) is not None and not isinstance(credits.get(key), bool):
                raise BalanceProviderError(
                    "schema_changed",
                    "SCHEMA_CHANGED",
                    "Codex 回應格式已變更，暫時無法解析額度資料",
                )
        plan_type = payload.get("plan_type")
        if plan_type is not None and not isinstance(plan_type, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        reached_type = payload.get("rate_limit_reached_type")
        if reached_type is not None and not isinstance(reached_type, str):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        retrieved_at = _timestamp(now)
        return BalanceResult(
            status="ok",
            source=source,
            balance=primary.remaining_percent,
            unit="%",
            used_percent=primary.used_percent,
            remaining_percent=primary.remaining_percent,
            reset_at=primary.resets_at,
            reset_after_seconds=(
                primary_data.get("reset_after_seconds")
                if isinstance(primary_data.get("reset_after_seconds"), int)
                and not isinstance(primary_data.get("reset_after_seconds"), bool)
                else None
            ),
            window_seconds=primary.window_seconds,
            retrieved_at=retrieved_at,
            last_success_at=retrieved_at,
            plan_type=plan_type,
            primary=primary,
            secondary=secondary,
            credits_balance=balance,
            has_credits=_optional_bool(credits.get("has_credits")),
            unlimited=_optional_bool(credits.get("unlimited")),
            rate_limit_reached_type=reached_type,
            spend_control_reached=_optional_bool(payload.get("spend_control")),
        )
    except BalanceProviderError as exc:
        return _result(
            status=exc.status,
            source=source,
            now=now,
            error_code=exc.error_code,
            error_message=exc.message,
        )


def _parse_json_lines(stdout: str | bytes | None, *, max_line_bytes: int) -> list[Mapping[str, Any]]:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if not isinstance(stdout, str):
        raise BalanceProviderError(
            "schema_changed",
            "SCHEMA_CHANGED",
            "Codex 回應格式已變更，暫時無法解析額度資料",
        )
    messages: list[Mapping[str, Any]] = []
    for line in stdout.splitlines():
        if len(line.encode("utf-8", errors="replace")) > max_line_bytes:
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            ) from exc
        if not isinstance(value, Mapping):
            raise BalanceProviderError(
                "schema_changed",
                "SCHEMA_CHANGED",
                "Codex 回應格式已變更，暫時無法解析額度資料",
            )
        messages.append(value)
    return messages


def _jsonrpc_error_result(
    response: Mapping[str, Any],
    *,
    source: Source,
    now: Callable[[], datetime],
) -> BalanceResult:
    error = response.get("error")
    message = error.get("message") if isinstance(error, Mapping) else None
    normalized = message.lower() if isinstance(message, str) else ""
    if "chatgpt authentication" in normalized:
        return _result(
            status="unsupported_auth",
            source=source,
            now=now,
            error_code="UNSUPPORTED_AUTH",
            error_message="目前登入方式沒有 ChatGPT Codex 額度資料",
        )
    if "authentication required" in normalized or "not logged" in normalized:
        return _result(
            status="auth_required",
            source=source,
            now=now,
            error_code="AUTH_REQUIRED",
            error_message="Codex 尚未登入或登入已失效，請先執行 codex login",
        )
    if "no snapshots" in normalized:
        return _result(
            status="service_error",
            source=source,
            now=now,
            error_code="NO_SNAPSHOT",
            error_message="Codex 目前沒有可用的額度資料",
        )
    return _result(
        status="service_error",
        source=source,
        now=now,
        error_code="APP_SERVER_ERROR",
        error_message="Codex 服務暫時無法提供額度資料",
    )


class AppServerBalanceClient:
    """Read Codex quota through the official local app-server."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        process_factory: Callable[..., Any] = subprocess.Popen,
        timeout: float = DEFAULT_APP_SERVER_TIMEOUT_SECONDS,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        now: Callable[[], datetime] = _utc_now,
        executable_name: str = "codex",
        client_version: str = "0.1.0",
        platform: str | None = None,
        home: str | Path | None = None,
    ) -> None:
        self.which = which
        self.process_factory = process_factory
        self.timeout = timeout
        self.max_line_bytes = max_line_bytes
        self.now = now
        self.executable_name = executable_name
        self.client_version = client_version
        self.platform = platform
        self.home = home

    @staticmethod
    def _request_lines(client_version: str) -> str:
        messages = [
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "codex_balance_tray",
                        "title": "Codex Balance Tray",
                        "version": client_version,
                    }
                },
            },
            {"method": "initialized", "params": {}},
            {"method": "account/read", "id": 2, "params": {"refreshToken": False}},
        ]
        return "\n".join(json.dumps(message, separators=(",", ":")) for message in messages) + "\n"

    @staticmethod
    def _account_allows_rate_limits(response: Mapping[str, Any]) -> bool:
        result = response.get("result")
        if not isinstance(result, Mapping):
            return False
        account = result.get("account")
        return (
            isinstance(account, Mapping)
            and account.get("type") == "chatgpt"
            and result.get("requiresOpenaiAuth") is not False
        )

    @staticmethod
    def _terminate_process(process: Any) -> None:
        try:
            process.kill()
        except (AttributeError, OSError, ValueError):
            pass
        try:
            process.wait(timeout=2)
        except (AttributeError, OSError, subprocess.TimeoutExpired, ValueError):
            pass

    def _communicate_interactively(self, process: Any, args: list[str]) -> list[Mapping[str, Any]]:
        """Run the JSON-RPC handshake without pipelining dependent requests."""

        stdin = getattr(process, "stdin", None)
        stdout = getattr(process, "stdout", None)
        stderr = getattr(process, "stderr", None)
        if stdin is None or not callable(getattr(stdout, "readline", None)):
            raise TypeError("process does not expose interactive stdio")

        output: queue.Queue[str | Exception | None] = queue.Queue()

        def read_stdout() -> None:
            try:
                while True:
                    line = stdout.readline()
                    if not line:
                        break
                    output.put(line)
            except Exception as exc:
                output.put(exc)
            finally:
                output.put(None)

        def drain_stderr() -> None:
            if stderr is None or not callable(getattr(stderr, "readline", None)):
                return
            try:
                while stderr.readline():
                    pass
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_stdout, name="codex-stdout-reader", daemon=True).start()
        threading.Thread(target=drain_stderr, name="codex-stderr-reader", daemon=True).start()

        deadline = time.monotonic() + self.timeout
        pending: dict[int, Mapping[str, Any]] = {}

        def send(message: Mapping[str, Any]) -> None:
            stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            stdin.flush()

        def read_response(expected_id: int) -> Mapping[str, Any]:
            response = pending.pop(expected_id, None)
            if response is not None:
                return response
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, self.timeout)
                try:
                    line = output.get(timeout=remaining)
                except queue.Empty:
                    raise subprocess.TimeoutExpired(args, self.timeout) from None
                if line is None:
                    raise OSError("Codex app-server closed stdout before responding")
                if isinstance(line, Exception):
                    raise line
                for message in _parse_json_lines(line, max_line_bytes=self.max_line_bytes):
                    message_id = message.get("id")
                    if message_id == expected_id:
                        return message
                    if isinstance(message_id, int):
                        pending[message_id] = message

        initialize = {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "codex_balance_tray",
                    "title": "Codex Balance Tray",
                    "version": self.client_version,
                }
            },
        }
        messages: list[Mapping[str, Any]] = []
        try:
            send(initialize)
            initialize_response = read_response(1)
            messages.append(initialize_response)
            if "error" not in initialize_response:
                send({"method": "initialized", "params": {}})
                send({"method": "account/read", "id": 2, "params": {"refreshToken": False}})
                account_response = read_response(2)
                messages.append(account_response)
                account_result = account_response.get("result")
                account = account_result.get("account") if isinstance(account_result, Mapping) else None
                requires_auth = account_result.get("requiresOpenaiAuth") if isinstance(account_result, Mapping) else None
                should_read_rates = (
                    account is None and requires_auth is False
                ) or (
                    isinstance(account, Mapping) and account.get("type") == "chatgpt"
                )
                if "error" not in account_response and should_read_rates:
                    send({"method": "account/rateLimits/read", "id": 3})
                    messages.append(read_response(3))
        except BaseException:
            self._terminate_process(process)
            raise
        finally:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            self._terminate_process(process)
        return messages

    def fetch(self) -> BalanceResult:
        executable = self.which(self.executable_name)
        if not executable and self.which is shutil.which:
            executable = find_codex_executable(
                self.executable_name,
                which=self.which,
                platform=self.platform,
                home=self.home,
            )
        if not executable:
            return _result(
                status="cli_unavailable",
                source="app_server",
                now=self.now,
                error_code="CLI_NOT_FOUND",
                error_message="找不到 Codex CLI，請安裝 Codex 或設定 PATH",
            )
        args = [executable, "app-server", "--listen", "stdio://"]
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
            "env": _subprocess_environment(
                executable,
                platform=self.platform,
                home=self.home,
            ),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            process = self.process_factory(args, **popen_kwargs)
        except (OSError, ValueError):
            return _result(
                status="cli_unavailable",
                source="app_server",
                now=self.now,
                error_code="CLI_START_FAILED",
                error_message="無法啟動 Codex app-server",
            )

        interactive = callable(getattr(getattr(process, "stdout", None), "readline", None)) and getattr(
            process, "stdin", None
        ) is not None
        try:
            if interactive:
                messages = self._communicate_interactively(process, args)
            else:
                stdout, _stderr = process.communicate(
                    input=self._request_lines(self.client_version),
                    timeout=self.timeout,
                )
                messages = _parse_json_lines(stdout, max_line_bytes=self.max_line_bytes)
        except BalanceProviderError as exc:
            return _result(
                status=exc.status,
                source="app_server",
                now=self.now,
                error_code=exc.error_code,
                error_message=exc.message,
            )
        except subprocess.TimeoutExpired:
            if not interactive:
                self._terminate_process(process)
            return _result(
                status="network_error",
                source="app_server",
                now=self.now,
                error_code="TIMEOUT",
                error_message="Codex app-server 回應逾時",
            )
        except (OSError, TypeError, ValueError):
            if not interactive:
                self._terminate_process(process)
            return _result(
                status="network_error",
                source="app_server",
                now=self.now,
                error_code="CLI_IO_ERROR",
                error_message="Codex app-server 通訊失敗",
            )

        by_id = {
            message.get("id"): message
            for message in messages
            if "id" in message and isinstance(message.get("id"), int)
        }
        for request_id in (1, 2, 3):
            response = by_id.get(request_id)
            if response is not None and "error" in response:
                return _jsonrpc_error_result(response, source="app_server", now=self.now)

        account_response = by_id.get(2)
        if not isinstance(account_response, Mapping):
            return _result(
                status="service_error",
                source="app_server",
                now=self.now,
                error_code="MISSING_RESPONSE",
                error_message="Codex app-server 沒有回傳完整額度資料",
            )
        account_result = account_response.get("result")
        if not isinstance(account_result, Mapping):
            return _result(
                status="schema_changed",
                source="app_server",
                now=self.now,
                error_code="SCHEMA_CHANGED",
                error_message="Codex 回應格式已變更，暫時無法解析額度資料",
            )
        # Some configured model providers make account/read intentionally
        # anonymous (account=null, requiresOpenaiAuth=false) while the same
        # app-server still returns the live ChatGPT rate-limit snapshot.
        # The quota response is the authoritative value for this tray.
        rate_response = by_id.get(3)
        if isinstance(rate_response, Mapping) and "error" not in rate_response:
            parsed_rate = parse_app_server_rate_limits(rate_response, now=self.now)
            if parsed_rate.status == "ok":
                return parsed_rate

        account = account_result.get("account")
        if account is None:
            return _result(
                status="auth_required",
                source="app_server",
                now=self.now,
                error_code="AUTH_REQUIRED",
                error_message="Codex 尚未登入或登入已失效，請先執行 codex login",
            )
        if not isinstance(account, Mapping):
            return _result(
                status="schema_changed",
                source="app_server",
                now=self.now,
                error_code="SCHEMA_CHANGED",
                error_message="Codex 回應格式已變更，暫時無法解析登入狀態",
            )
        if account.get("type") != "chatgpt" or account_result.get("requiresOpenaiAuth") is False:
            return _result(
                status="unsupported_auth",
                source="app_server",
                now=self.now,
                error_code="UNSUPPORTED_AUTH",
                error_message="目前登入方式沒有 ChatGPT Codex 額度資料",
            )

        if not isinstance(rate_response, Mapping):
            return _result(
                status="service_error",
                source="app_server",
                now=self.now,
                error_code="MISSING_RESPONSE",
                error_message="Codex app-server 沒有回傳完整額度資料",
            )

        return parse_app_server_rate_limits(rate_response, now=self.now)

    def get_balance(self) -> BalanceResult:
        return self.fetch()


CredentialProvider = Callable[[], tuple[str, str]]


def _default_auth_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _load_file_credentials(path: Path) -> tuple[str, str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        tokens = data.get("tokens") if isinstance(data, Mapping) else None
        token = tokens.get("access_token") if isinstance(tokens, Mapping) else None
        account_id = tokens.get("account_id") if isinstance(tokens, Mapping) else None
    except (OSError, ValueError, TypeError):
        raise BalanceProviderError(
            "auth_required",
            "AUTH_REQUIRED",
            "無法讀取 Codex 登入資料，請先在 Codex 重新登入",
        ) from None
    if not isinstance(token, str) or not token or not isinstance(account_id, str) or not account_id:
        raise BalanceProviderError(
            "auth_required",
            "AUTH_REQUIRED",
            "Codex 登入資料不完整，請先在 Codex 重新登入",
        )
    return token, account_id


def _credentials_from_provider(provider: Callable[[], Any]) -> tuple[str, str]:
    try:
        value = provider()
    except BalanceProviderError as exc:
        raise _safe_provider_error(exc) from None
    except Exception:
        raise BalanceProviderError(
            "auth_required",
            "AUTH_REQUIRED",
            "無法取得 Codex 登入資料，請先在 Codex 重新登入",
        ) from None
    if isinstance(value, Mapping):
        credentials = value.get("tokens") if isinstance(value.get("tokens"), Mapping) else value
        token = credentials.get("access_token")
        account_id = credentials.get("account_id")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
        token, account_id = value
    else:
        token = account_id = None
    if not isinstance(token, str) or not token or not isinstance(account_id, str) or not account_id:
        raise BalanceProviderError(
            "auth_required",
            "AUTH_REQUIRED",
            "Codex 登入資料不完整，請先在 Codex 重新登入",
        )
    return token, account_id


def _retry_after(
    headers: Any,
    *,
    maximum: int | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> int | None:
    # ``maximum`` is retained for compatibility with older callers, but a
    # client-side cap must never shorten a server-provided minimum.
    _ = maximum
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is None:
            return None
        text = str(raw).strip()
        try:
            value = int(text)
        except ValueError:
            retry_at = parsedate_to_datetime(text)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            current = now()
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            value = math.ceil((retry_at - current).total_seconds())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    # A server-provided Retry-After is a minimum and must not be shortened by
    # a client-side cap. RetryPolicy.delay applies no jitter or cap either.
    return max(0, value)


class HttpBalanceClient:
    """Explicit private-HTTP fallback with safe response classification."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        base_url: str = "https://chatgpt.com/backend-api",
        credential_provider: Callable[[], Any] | None = None,
        auth_path: Path | None = None,
        allow_file_auth: bool = False,
        allow_insecure_http: bool = False,
        allow_untrusted_base_url: bool = False,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        now: Callable[[], datetime] = _utc_now,
        max_retry_after_seconds: int = DEFAULT_MAX_RETRY_AFTER_SECONDS,
    ) -> None:
        self.session = session or requests.Session()
        self.base_url = self._normalize_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
            allow_untrusted_base_url=allow_untrusted_base_url,
        )
        self.credential_provider = credential_provider
        self.auth_path = auth_path
        self.allow_file_auth = allow_file_auth
        self.timeout = timeout
        self.now = now
        self.max_retry_after_seconds = max_retry_after_seconds

    @staticmethod
    def _normalize_base_url(
        base_url: str,
        *,
        allow_insecure_http: bool = False,
        allow_untrusted_base_url: bool = False,
    ) -> str:
        value = base_url.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"}:
            raise ValueError("base_url 必須使用 HTTPS URL")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError("HTTP fallback 預設只允許 HTTPS；若為受信任私有端點請明確啟用")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url 必須是沒有認證、query 或 fragment 的 URL")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("base_url 必須使用有效的 port") from None
        host = parsed.hostname.lower()
        if not allow_untrusted_base_url and host not in _TRUSTED_HTTP_HOSTS:
            raise ValueError("HTTP fallback 預設只允許官方 host；私有端點請明確啟用")
        if not allow_untrusted_base_url and host in _TRUSTED_HTTP_HOSTS:
            default_port = 443 if parsed.scheme == "https" else 80
            if port is not None and port != default_port:
                raise ValueError("官方 HTTP fallback 不允許非預設 port")
        if host in _CHATGPT_HTTP_HOSTS and not parsed.path.rstrip("/"):
            value += "/backend-api"
        return value

    @property
    def endpoint(self) -> str:
        path = urlsplit(self.base_url).path.rstrip("/")
        if path.endswith("/backend-api"):
            return f"{self.base_url}/wham/usage"
        return f"{self.base_url}/api/codex/usage"

    def _credentials(self) -> tuple[str, str]:
        if self.credential_provider is not None:
            return _credentials_from_provider(self.credential_provider)
        if self.allow_file_auth:
            path = Path(self.auth_path) if self.auth_path is not None else _default_auth_path()
            return _load_file_credentials(path)
        raise BalanceProviderError(
            "unsupported_auth",
            "UNSUPPORTED_AUTH",
            "HTTP fallback 未設定安全的 Codex 認證來源",
        )

    def fetch(self) -> BalanceResult:
        try:
            token, account_id = self._credentials()
        except BalanceProviderError as exc:
            safe_exc = _safe_provider_error(exc)
            return _result(
                status=safe_exc.status,
                source="chatgpt_backend",
                now=self.now,
                error_code=safe_exc.error_code,
                error_message=safe_exc.message,
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "ChatGPT-Account-Id": account_id,
            "Accept": "application/json",
            "User-Agent": "codex-tray/0.1",
        }
        try:
            response = self.session.get(
                self.endpoint,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except (requests.RequestException, TimeoutError, OSError, ValueError):
            return _result(
                status="network_error",
                source="chatgpt_backend",
                now=self.now,
                error_code="NETWORK_ERROR",
                error_message="網路暫時無法取得 Codex 額度",
            )
        status_code = getattr(response, "status_code", None)
        if status_code in (401, 403):
            return _result(
                status="auth_required",
                source="chatgpt_backend",
                now=self.now,
                error_code=f"HTTP_{status_code}",
                error_message="Codex 尚未登入或登入已失效，請先重新登入",
            )
        if isinstance(status_code, int) and 300 <= status_code < 400:
            return _result(
                status="unsupported_route",
                source="chatgpt_backend",
                now=self.now,
                error_code=f"HTTP_{status_code}",
                error_message="Codex 額度服務重新導向被拒絕",
            )
        if status_code in (404, 405):
            return _result(
                status="unsupported_route",
                source="chatgpt_backend",
                now=self.now,
                error_code=f"HTTP_{status_code}",
                error_message="Codex 版本或額度 endpoint 不相容",
            )
        if status_code == 429:
            retry_after = _retry_after(
                getattr(response, "headers", None),
                now=self.now,
            )
            return _result(
                status="rate_limited",
                source="chatgpt_backend",
                now=self.now,
                error_code="HTTP_429",
                error_message="Codex 服務要求稍後重試",
                retry_after_seconds=retry_after,
            )
        if not isinstance(status_code, int) or status_code >= 500:
            code = f"HTTP_{status_code}" if isinstance(status_code, int) else "HTTP_ERROR"
            retry_after = _retry_after(
                getattr(response, "headers", None),
                now=self.now,
            )
            return _result(
                status="service_error",
                source="chatgpt_backend",
                now=self.now,
                error_code=code,
                error_message="Codex 服務暫時無法提供額度資料",
                retry_after_seconds=retry_after,
            )
        if status_code != 200:
            return _result(
                status="service_error",
                source="chatgpt_backend",
                now=self.now,
                error_code=f"HTTP_{status_code}",
                error_message="Codex 額度服務回傳錯誤",
            )
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return _result(
                status="schema_changed",
                source="chatgpt_backend",
                now=self.now,
                error_code="SCHEMA_CHANGED",
                error_message="Codex 回應格式已變更，暫時無法解析額度資料",
            )
        if not isinstance(payload, Mapping):
            return _result(
                status="schema_changed",
                source="chatgpt_backend",
                now=self.now,
                error_code="SCHEMA_CHANGED",
                error_message="Codex 回應格式已變更，暫時無法解析額度資料",
            )
        return parse_http_usage_payload(payload, now=self.now)

    def get_balance(self) -> BalanceResult:
        return self.fetch()


class CodexBalanceProvider:
    """Coordinate the primary app-server and an optional HTTP fallback.

    Calls are single-flight.  Transient failures are retried with bounded
    exponential backoff; after a failed refresh, the last successful result is
    returned as ``stale`` without discarding its displayable balance.
    """

    def __init__(
        self,
        *,
        app_server: Any | None = None,
        http_fallback: Any | None = None,
        fallback: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.app_server = app_server or AppServerBalanceClient(now=now)
        self.http_fallback = http_fallback if http_fallback is not None else fallback
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleep = sleep
        self.random_value = random_value
        self.now = now
        self._condition = threading.Condition()
        self._inflight = False
        self._inflight_result: BalanceResult | None = None
        self._last_success: BalanceResult | None = None

    @staticmethod
    def _should_retry(result: BalanceResult) -> bool:
        # A malformed adapter result is a local contract/schema failure, not a
        # transient service outage. Retrying it only repeats the same invalid
        # response and can hide a broken transport adapter behind backoff.
        code = result.error_code or ""
        if code in {"INVALID_PROVIDER_RESULT", "PROVIDER_ERROR"}:
            return False
        if result.status in {"network_error", "rate_limited"}:
            return True
        if result.status != "service_error":
            return False
        if code.startswith("HTTP_"):
            try:
                return int(code.removeprefix("HTTP_")) >= 500
            except ValueError:
                return False
        return code in {"APP_SERVER_ERROR", "NO_SNAPSHOT", "SERVICE_UNAVAILABLE"}

    def _fetch_once(self, client: Any) -> BalanceResult:
        source: Source = "chatgpt_backend" if isinstance(client, HttpBalanceClient) else "app_server"
        try:
            result = client.fetch()
        except BalanceProviderError as exc:
            safe_exc = _safe_provider_error(exc)
            return _result(
                status=safe_exc.status,
                source=source,
                now=self.now,
                error_code=safe_exc.error_code,
                error_message=safe_exc.message,
            )
        except (requests.RequestException, TimeoutError, OSError):
            return _result(
                status="network_error",
                source=source,
                now=self.now,
                error_code="NETWORK_ERROR",
                error_message="網路暫時無法取得 Codex 額度",
            )
        except Exception:
            # Provider adapters are a trust boundary. Do not let an arbitrary
            # exception (which may contain a URL or a credential) reach the UI.
            return _result(
                status="service_error",
                source=source,
                now=self.now,
                error_code="PROVIDER_ERROR",
                error_message="Codex 服務暫時無法提供額度資料",
            )
        if not isinstance(result, BalanceResult):
            return _result(
                status="service_error",
                source=source,
                now=self.now,
                error_code="INVALID_PROVIDER_RESULT",
                error_message="餘額資料提供器回傳了無效結果",
            )
        return result

    def _fetch_with_retries(self, client: Any) -> BalanceResult:
        result = self._fetch_once(client)
        for attempt in range(1, self.retry_policy.max_attempts):
            if not self._should_retry(result):
                return result
            delay = self.retry_policy.delay(
                attempt,
                retry_after_seconds=result.retry_after_seconds,
                random_value=self.random_value,
            )
            self.sleep(delay)
            result = self._fetch_once(client)
        return result

    def _stale_result(self, result: BalanceResult) -> BalanceResult:
        if self._last_success is None:
            return result
        last_success_at = self._last_success.last_success_at or self._last_success.retrieved_at
        return replace(
            self._last_success,
            status="stale",
            source=result.source,
            retrieved_at=result.retrieved_at or _timestamp(self.now),
            last_success_at=last_success_at,
            retry_after_seconds=result.retry_after_seconds,
            error_code=result.error_code,
            error_message=result.error_message,
        )

    def fetch(self) -> BalanceResult:
        with self._condition:
            if self._inflight:
                while self._inflight:
                    self._condition.wait()
                if self._inflight_result is not None:
                    return self._inflight_result
            self._inflight = True
        try:
            result = self._fetch_with_retries(self.app_server)
            if result.status != "ok" and self.http_fallback is not None and result.status in {
                "cli_unavailable",
                "network_error",
                "service_error",
                "unsupported_route",
                "schema_changed",
            }:
                result = self._fetch_with_retries(self.http_fallback)
            if result.status == "ok":
                result = replace(
                    result,
                    last_success_at=result.retrieved_at,
                )
                self._last_success = result
            else:
                result = self._stale_result(result)
            with self._condition:
                self._inflight_result = result
                self._inflight = False
                self._condition.notify_all()
            return result
        except BaseException:
            with self._condition:
                self._inflight = False
                self._condition.notify_all()
            raise

    def get_balance(self) -> BalanceResult:
        return self.fetch()

    def fetch_balance(self) -> BalanceResult:
        return self.fetch()


# Names kept intentionally broad so the provider can be adopted without
# coupling callers to whether the source is app-server or HTTP.
BalanceProvider = CodexBalanceProvider
AppServerClient = AppServerBalanceClient
HttpUsageClient = HttpBalanceClient
parse_app_server_payload = parse_app_server_rate_limits
parse_usage_payload = parse_http_usage_payload
parse_app_server_response = parse_app_server_rate_limits
parse_http_payload = parse_http_usage_payload
AppServerProvider = AppServerBalanceClient
HttpProvider = HttpBalanceClient
CodexUsageProvider = CodexBalanceProvider
UsageProvider = CodexBalanceProvider

__all__ = [
    "AppServerBalanceClient",
    "AppServerClient",
    "AppServerProvider",
    "BalanceProvider",
    "BalanceProviderError",
    "BalanceResult",
    "CodexBalanceProvider",
    "CodexUsageProvider",
    "HttpBalanceClient",
    "HttpProvider",
    "HttpUsageClient",
    "QuotaWindow",
    "RetryPolicy",
    "UsageProvider",
    "parse_app_server_payload",
    "parse_app_server_rate_limits",
    "parse_app_server_response",
    "parse_http_payload",
    "parse_http_usage_payload",
    "parse_usage_payload",
]
