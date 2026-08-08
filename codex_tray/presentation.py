"""Human-readable strings used by the notification-area UI.

This module deliberately accepts both the legacy ``UsageSnapshot`` and the
provider-neutral ``BalanceResult``. The tray never needs to know whether the
provider used app-server JSON-RPC, an HTTP compatibility adapter, or a mock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

from .forecast import calculate_usage_forecast


_PLAN_NAMES = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
}

_STATUS_MESSAGES = {
    "loading": "正在取得 Codex 額度資料…",
    "auth_required": "尚未登入 Codex",
    "unsupported_auth": "目前登入方式沒有 ChatGPT Codex 額度資料",
    "unsupported_route": "Codex 版本／endpoint 不相容",
    "network_error": "網路暫時無法取得 Codex 額度",
    "rate_limited": "Codex 服務暫時限流",
    "cli_unavailable": "找不到 Codex CLI",
    "schema_changed": "Codex 回應格式已變更",
    "service_error": "Codex 服務暫時無法提供額度",
    "unavailable": "目前沒有可用的額度資料",
}

# Windows Shell_NotifyIcon rejects tooltip strings longer than 128 characters.
MAX_TOOLTIP_LENGTH = 128

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)([\"']?authorization[\"']?\s*[:=]\s*[\"']?)([^\"'\r\n,;}]+?)([\"']?)(?=\s*(?:[,;}\]]|$))"
    ),
    re.compile(r"(?i)(\b[\"']?bearer\s+)[^\"'\s,;}]+([\"']?)"),
    re.compile(
        r"(?i)([\"']?(?:access_token|refresh_token|api_key|account_id|accessToken|refreshToken|apiKey|accountId|chatgpt-account-id|access-token|refresh-token|api-key|account-id|x-api-key|x-api-token)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)([\"']?)"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
)


def _value(result: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(result, name):
            value = getattr(result, name)
            if value is not None:
                return value
    return default


def _status(result: Any, override: str | None = None) -> str:
    if override:
        return override
    status = _value(result, "status")
    if status:
        return str(status)
    return "ok" if not _value(result, "error", "error_message") else "unavailable"


def _plan_name(plan_type: str | None) -> str:
    if not plan_type:
        return "未知方案"
    return _PLAN_NAMES.get(plan_type.lower(), plan_type.replace("_", " ").title())


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _remaining_value(result: Any) -> float | int | None:
    value = _value(result, "remaining_percent", "remaining", "balance")
    value = _number(value)
    if value is None:
        return None
    unit = str(_value(result, "unit", default="%"))
    if unit == "%" and not 0 <= value <= 100:
        return None
    return value


def _remaining_text(result: Any) -> str:
    if _value(result, "unlimited") is True:
        return "無上限"
    value = _remaining_value(result)
    if value is None:
        return "--"
    unit = str(_value(result, "unit", default="%"))
    return f"{value:g}{unit}" if isinstance(value, float) else f"{value}{unit}"


def format_remaining(snapshot: Any) -> str:
    """Return a safe remaining value without inventing a percentage."""

    return _remaining_text(snapshot)


def format_tray_title(snapshot: Any, *, status: str | None = None) -> str:
    """Short text rendered into the tray icon.

    A stale value gets an asterisk so the user cannot mistake cached data for
    a live response. Missing data is always ``--`` rather than 100%.
    """

    if _value(snapshot, "unlimited") is True:
        title = "∞"
    else:
        title = _remaining_text(snapshot)
    if _status(snapshot, status) == "stale" and title not in {"--", "∞"}:
        return f"{title}*"
    return title


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "未知"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "未知"
    if seconds <= 0:
        return "即將重置"

    days, remainder = divmod(seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小時")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes} 分鐘")
    return " ".join(parts) or "不到 1 分鐘"


def _format_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            value = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = str(value).strip()
    return text or None


def status_message(status: str) -> str:
    return _STATUS_MESSAGES.get(status, "目前沒有可用的額度資料")


def redact_error_message(value: Any) -> str:
    """Keep provider failures useful without placing credentials in the UI."""

    text = str(value).strip()
    for pattern in _SECRET_PATTERNS:
        def replace(match: re.Match[str]) -> str:
            if match.re.groups >= 3:
                return f"{match.group(1)}[REDACTED]{match.group(3) or ''}"
            if match.re.groups == 2:
                return f"{match.group(1)}[REDACTED]{match.group(2) or ''}"
            return "[REDACTED]"

        text = pattern.sub(replace, text)
    return text[:240]


def _format_forecast_percent(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    rounded = math.floor(value * 10 + 0.5) / 10
    if rounded == 0:
        return "0"
    return f"{rounded:.1f}".rstrip("0").rstrip(".")


def _forecast_lines(snapshot: Any) -> list[str]:
    forecast = calculate_usage_forecast(snapshot)
    if forecast.status == "insufficient_data":
        return []

    lines = [f"平均消耗：{_format_forecast_percent(forecast.average_daily_percent)}%／天"]

    if forecast.estimated_exhaustion_seconds is not None:
        lines.append(f"預估 {format_duration(forecast.estimated_exhaustion_seconds)}後用完")
    elif forecast.status == "no_usage":
        lines.append("預估耗盡：目前未觀測到消耗")

    projected = forecast.projected_remaining_percent
    if projected is not None and forecast.status == "at_risk":
        lines.append(
            "重置前：可能提前用完"
            f"（約超出 {_format_forecast_percent(abs(projected))}%）"
        )
        lines.append("判斷：可能在 reset 前用完")
    elif projected is not None:
        lines.append(f"重置前：約 {_format_forecast_percent(projected)}%")
        if forecast.status == "no_usage":
            lines.append("判斷：目前未觀測到消耗")
        else:
            lines.append("判斷：照目前速度可撐到 reset")
    return lines


def format_tooltip(
    snapshot: Any,
    *,
    status: str | None = None,
    error_message: str | None = None,
    last_success_at: Any = None,
) -> str:
    """Format only display-safe fields from a provider result."""

    current_status = _status(snapshot, status)
    remaining = _remaining_text(snapshot)
    has_value = remaining not in {"--", "未知"}
    plan_type = _value(snapshot, "plan_type", "plan")
    plan = _plan_name(str(plan_type) if plan_type is not None else None)
    lines: list[str] = []

    if has_value:
        display_remaining = f"{remaining}*" if current_status == "stale" else remaining
        lines.append(f"Codex 剩餘 {display_remaining} · {plan}")
    else:
        lines.append(f"Codex 額度：{status_message(current_status)}")

    if current_status == "stale":
        lines.append("資料過期（顯示上次成功資料）")

    used = _value(snapshot, "used_percent", "used")
    if used is not None:
        used = _number(used)
        if used is not None:
            unit = str(_value(snapshot, "unit", default="%"))
            lines.append(f"使用率：{used:g}{unit}" if isinstance(used, float) else f"使用率：{used}{unit}")

    reset_after = _value(snapshot, "reset_after_seconds")
    if reset_after is not None:
        lines.append(f"距離重置：{format_duration(reset_after)}")
    else:
        reset_at = _value(snapshot, "reset_at")
        if reset_at is not None:
            lines.append(f"重置時間：{_format_timestamp(reset_at) or '未知'}")

    if current_status in {"ok", "stale"}:
        lines.extend(_forecast_lines(snapshot))

    credits = _value(snapshot, "credits_balance")
    if credits is not None:
        lines.append(f"Credits 餘額：{credits}")
    if _value(snapshot, "overage_limit_reached") is True:
        lines.append("已達超額上限")

    success_time = last_success_at or _value(snapshot, "last_success_at", "retrieved_at", "fetched_at")
    if current_status == "stale" or (not has_value and success_time is not None):
        formatted = _format_timestamp(success_time)
        if formatted:
            lines.append(f"上次成功：{formatted}")

    safe_error = error_message or _value(snapshot, "error", "error_message")
    if safe_error:
        lines.append(f"原因：{redact_error_message(safe_error)}")

    text = "\n".join(lines)
    if len(text) <= MAX_TOOLTIP_LENGTH:
        return text
    return text[: MAX_TOOLTIP_LENGTH - 1] + "…"


__all__ = [
    "format_duration",
    "format_remaining",
    "format_tooltip",
    "format_tray_title",
    "redact_error_message",
    "status_message",
]
