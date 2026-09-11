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
from .i18n import (
    DEFAULT_LOCALE,
    localized_error_key,
    normalize_locale,
    translate,
)


_PLAN_NAMES = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
}

_STATUS_NAMES = frozenset(
    {
        "loading",
        "auth_required",
        "unsupported_auth",
        "unsupported_route",
        "network_error",
        "rate_limited",
        "cli_unavailable",
        "schema_changed",
        "service_error",
        "unavailable",
    }
)

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


def _plan_name(plan_type: str | None, locale: Any = DEFAULT_LOCALE) -> str:
    if not plan_type:
        return translate("plan.unknown", locale)
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


def _remaining_text(result: Any, locale: Any = DEFAULT_LOCALE) -> str:
    if _value(result, "unlimited") is True:
        return translate("remaining.unlimited", locale)
    value = _remaining_value(result)
    if value is None:
        return "--"
    unit = str(_value(result, "unit", default="%"))
    return f"{value:g}{unit}" if isinstance(value, float) else f"{value}{unit}"


def format_remaining(snapshot: Any, *, locale: Any = DEFAULT_LOCALE) -> str:
    """Return a safe remaining value without inventing a percentage."""

    return _remaining_text(snapshot, locale)


def format_tray_title(
    snapshot: Any,
    *,
    status: str | None = None,
    locale: Any = DEFAULT_LOCALE,
) -> str:
    """Short text rendered into the tray icon.

    A stale value gets an asterisk so the user cannot mistake cached data for
    a live response. Missing data is always ``--`` rather than 100%.
    """

    if _value(snapshot, "unlimited") is True:
        title = "∞"
    else:
        title = _remaining_text(snapshot, locale)
    if _status(snapshot, status) == "stale" and title not in {"--", "∞"}:
        return f"{title}*"
    return title


def _duration_unit(value: int, unit: str, locale: Any) -> str:
    selected = normalize_locale(locale)
    labels = {
        "zh-TW": {"day": "天", "hour": "小時", "minute": "分鐘"},
        "zh-CN": {"day": "天", "hour": "小时", "minute": "分钟"},
        "ja-JP": {"day": "日", "hour": "時間", "minute": "分"},
    }.get(selected)
    if labels is not None:
        return f"{value} {labels[unit]}"
    label = unit if value == 1 else f"{unit}s"
    return f"{value} {label}"


def format_duration(seconds: int | float | None, locale: Any = DEFAULT_LOCALE) -> str:
    if seconds is None:
        return translate("duration.unknown", locale)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return translate("duration.unknown", locale)
    if seconds <= 0:
        return translate("duration.resetting_soon", locale)

    days, remainder = divmod(seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(_duration_unit(days, "day", locale))
    if hours:
        parts.append(_duration_unit(hours, "hour", locale))
    if minutes and len(parts) < 2:
        parts.append(_duration_unit(minutes, "minute", locale))
    return " ".join(parts) or translate("duration.less_than_minute", locale)


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


def status_message(status: str, locale: Any = DEFAULT_LOCALE) -> str:
    key = f"status.{status}"
    return translate(key if status in _STATUS_NAMES else "status.unavailable", locale)


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


def _localized_error_message(
    value: Any,
    *,
    locale: Any,
    status: str,
    error_code: Any = None,
) -> str:
    text = str(value).strip()
    safe_text = redact_error_message(text)
    # Preserve redaction evidence instead of replacing a credential-bearing
    # message with a generic status string. Compact long diagnostic blobs so
    # every redaction marker remains visible within the Windows tooltip limit.
    if safe_text != text or "[REDACTED]" in safe_text:
        redaction_count = safe_text.count("[REDACTED]")
        if redaction_count > 1 and len(safe_text) > 80:
            return " ".join("[REDACTED]" for _ in range(redaction_count))
        return safe_text
    key = localized_error_key(status=status, error_code=error_code, message=text)
    if key:
        return translate(key, locale)
    return safe_text


def _format_forecast_percent(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    rounded = math.floor(value * 10 + 0.5) / 10
    if rounded == 0:
        return "0"
    return f"{rounded:.1f}".rstrip("0").rstrip(".")


def _forecast_lines(snapshot: Any, locale: Any) -> list[str]:
    forecast = calculate_usage_forecast(snapshot)
    if forecast.status == "insufficient_data":
        return []

    average = _format_forecast_percent(forecast.average_daily_percent)
    projected = forecast.projected_remaining_percent
    if normalize_locale(locale) == "en":
        if forecast.estimated_exhaustion_seconds is not None:
            lines = [
                translate(
                    "forecast.compact_rate",
                    locale,
                    value=average,
                    duration=format_duration(forecast.estimated_exhaustion_seconds, locale),
                )
            ]
        else:
            lines = [translate("forecast.compact_rate_no_usage", locale, value=average)]
        if projected is not None and forecast.status == "at_risk":
            lines.append(translate("forecast.compact_risk", locale))
        elif projected is not None:
            key = "forecast.compact_reset_no_usage" if forecast.status == "no_usage" else "forecast.compact_reset"
            lines.append(translate(key, locale, value=_format_forecast_percent(projected)))
        return lines

    lines = [translate("forecast.average", locale, value=average)]
    if forecast.estimated_exhaustion_seconds is not None:
        lines.append(
            translate(
                "forecast.exhaustion",
                locale,
                duration=format_duration(forecast.estimated_exhaustion_seconds, locale),
            )
        )
    elif forecast.status == "no_usage":
        lines.append(translate("forecast.no_usage", locale))

    if projected is not None and forecast.status == "at_risk":
        lines.append(
            translate(
                "forecast.at_risk",
                locale,
                value=_format_forecast_percent(abs(projected)),
            )
        )
        lines.append(translate("forecast.judgment_risk", locale))
    elif projected is not None:
        lines.append(translate("forecast.projected", locale, value=_format_forecast_percent(projected)))
        if forecast.status == "no_usage":
            lines.append(translate("forecast.judgment_no_usage", locale))
        else:
            lines.append(translate("forecast.judgment_on_track", locale))
    return lines


def _quota_window_lines(snapshot: Any, locale: Any) -> list[str]:
    """Show both live buckets without deriving one from the other."""

    lines: list[str] = []
    for window in (_value(snapshot, "primary"), _value(snapshot, "secondary")):
        if window is None:
            continue
        seconds = _value(window, "window_seconds")
        remaining = _value(window, "remaining_percent")
        if seconds is None or remaining is None:
            continue
        remaining_text = f"{remaining}%"
        if 4 * 60 * 60 <= seconds <= 6 * 60 * 60:
            lines.append(translate("tooltip.five_hour", locale, remaining=remaining_text))
            reset_after = _value(window, "reset_after_seconds")
            if reset_after is not None:
                lines.append(
                    translate("tooltip.five_hour_reset", locale, duration=format_duration(reset_after, locale))
                )
        elif 6 * 24 * 60 * 60 <= seconds <= 8 * 24 * 60 * 60:
            lines.append(translate("tooltip.weekly", locale, remaining=remaining_text))
            reset_after = _value(window, "reset_after_seconds")
            if reset_after is not None:
                lines.append(
                    translate("tooltip.weekly_reset", locale, duration=format_duration(reset_after, locale))
                )
    return lines


def format_tooltip(
    snapshot: Any,
    *,
    status: str | None = None,
    error_message: str | None = None,
    last_success_at: Any = None,
    locale: Any = DEFAULT_LOCALE,
) -> str:
    """Format only display-safe fields from a provider result."""

    current_status = _status(snapshot, status)
    remaining = _remaining_text(snapshot, locale)
    has_value = _value(snapshot, "unlimited") is True or _remaining_value(snapshot) is not None
    plan_type = _value(snapshot, "plan_type", "plan")
    plan = _plan_name(str(plan_type) if plan_type is not None else None, locale)
    lines: list[str] = []

    if has_value:
        display_remaining = f"{remaining}*" if current_status == "stale" else remaining
        lines.append(translate("tooltip.remaining", locale, remaining=display_remaining, plan=plan))
    else:
        lines.append(translate("tooltip.quota_status", locale, status=status_message(current_status, locale)))

    if current_status == "stale":
        lines.append(translate("tooltip.stale", locale))

    if has_value:
        lines.extend(_quota_window_lines(snapshot, locale))

    used = _value(snapshot, "used_percent", "used")
    if used is not None:
        used = _number(used)
        if used is not None:
            unit = str(_value(snapshot, "unit", default="%"))
            display_used = f"{used:g}{unit}" if isinstance(used, float) else f"{used}{unit}"
            lines.append(translate("tooltip.usage", locale, used=display_used, unit=""))

    if current_status in {"ok", "stale"}:
        lines.extend(_forecast_lines(snapshot, locale))

    credits = _value(snapshot, "credits_balance")
    if credits is not None:
        lines.append(translate("tooltip.credits", locale, credits=credits))
    if _value(snapshot, "overage_limit_reached") is True:
        lines.append(translate("tooltip.overage", locale))

    safe_error = error_message or _value(snapshot, "error", "error_message")
    if safe_error:
        message = _localized_error_message(
            safe_error,
            locale=locale,
            status=current_status,
            error_code=_value(snapshot, "error_code"),
        )
        lines.append(translate("tooltip.reason", locale, message=message))

    success_time = last_success_at or _value(snapshot, "last_success_at", "retrieved_at", "fetched_at")
    if current_status == "stale" or (not has_value and success_time is not None):
        formatted = _format_timestamp(success_time)
        if formatted:
            lines.append(translate("tooltip.last_success", locale, time=formatted))

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
