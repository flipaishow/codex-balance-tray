"""Small, conservative quota exhaustion forecasts for the tray UI.

The Codex rate-limit response exposes a percentage and the current reset
window, not a guaranteed future consumption model. This module therefore only
provides a labelled linear estimate from the current window's observed
average; it never treats missing fields as zero usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Literal


SECONDS_PER_DAY = 24 * 60 * 60
MIN_FORECAST_ELAPSED_SECONDS = 60 * 60
ForecastStatus = Literal["insufficient_data", "no_usage", "on_track", "at_risk"]


@dataclass(frozen=True)
class UsageForecast:
    """A display-safe linear estimate for the active quota window."""

    status: ForecastStatus
    reset_after_seconds: int | None = None
    elapsed_seconds: int | None = None
    average_daily_percent: float | None = None
    estimated_exhaustion_seconds: int | None = None
    projected_remaining_percent: float | None = None


def _value(result: Any, *names: str) -> Any:
    for name in names:
        value = getattr(result, name, None)
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> float | None:
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _primary_value(result: Any, name: str) -> Any:
    primary = getattr(result, "primary", None)
    return getattr(primary, name, None) if primary is not None else None


def _reset_after_seconds(result: Any) -> int | None:
    raw = _number(_value(result, "reset_after_seconds"))
    if raw is None:
        reset_at = _number(_value(result, "reset_at"))
        sample_at = _timestamp(_value(result, "retrieved_at", "last_success_at"))
        if reset_at is not None and sample_at is not None:
            raw = reset_at - sample_at
    if raw is None:
        return None
    return max(0, int(raw))


def _window_seconds(result: Any) -> int | None:
    raw = _number(_value(result, "window_seconds", "limit_window_seconds"))
    if raw is None:
        raw = _number(_primary_value(result, "window_seconds"))
    if raw is None:
        return None
    return int(raw) if raw > 0 else None


def _percent(result: Any, *names: str) -> float | None:
    value = _number(_value(result, *names))
    if value is None:
        primary = getattr(result, "primary", None)
        for name in names:
            value = _number(getattr(primary, name, None)) if primary is not None else None
            if value is not None:
                break
    if value is None or not 0 <= value <= 100:
        return None
    return value


def calculate_usage_forecast(
    result: Any,
    *,
    minimum_elapsed_seconds: int = MIN_FORECAST_ELAPSED_SECONDS,
) -> UsageForecast:
    """Estimate whether the current rate reaches zero before the next reset.

    ``used_percent`` is divided by the elapsed portion of the current quota
    window. The estimate is intentionally withheld during the first hour (or
    when the server does not provide a complete window), because a single
    early burst is too noisy to present as a confident forecast.
    """

    unit = str(_value(result, "unit") or "%")
    if unit != "%":
        return UsageForecast(status="insufficient_data")

    used = _percent(result, "used_percent", "used")
    remaining = _percent(result, "remaining_percent", "remaining", "balance")
    if remaining is None and used is not None:
        remaining = 100 - used
    reset_after = _reset_after_seconds(result)
    window = _window_seconds(result)
    if used is None or remaining is None or reset_after is None or window is None:
        return UsageForecast(status="insufficient_data", reset_after_seconds=reset_after)
    if reset_after > window or reset_after <= 0:
        return UsageForecast(status="insufficient_data", reset_after_seconds=reset_after)

    elapsed = window - reset_after
    if elapsed < max(0, int(minimum_elapsed_seconds)):
        return UsageForecast(
            status="insufficient_data",
            reset_after_seconds=reset_after,
            elapsed_seconds=elapsed,
        )

    if used == 0:
        return UsageForecast(
            status="no_usage",
            reset_after_seconds=reset_after,
            elapsed_seconds=elapsed,
            average_daily_percent=0.0,
            projected_remaining_percent=remaining,
        )

    average_daily = used / (elapsed / SECONDS_PER_DAY)
    projected_remaining = remaining - average_daily * (reset_after / SECONDS_PER_DAY)
    exhaustion_seconds = max(1, round(remaining / average_daily * SECONDS_PER_DAY))
    status: ForecastStatus = "at_risk" if projected_remaining <= 0 else "on_track"
    return UsageForecast(
        status=status,
        reset_after_seconds=reset_after,
        elapsed_seconds=elapsed,
        average_daily_percent=average_daily,
        estimated_exhaustion_seconds=exhaustion_seconds,
        projected_remaining_percent=projected_remaining,
    )


__all__ = [
    "MIN_FORECAST_ELAPSED_SECONDS",
    "UsageForecast",
    "calculate_usage_forecast",
]
