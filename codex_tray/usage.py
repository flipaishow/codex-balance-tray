"""Codex usage response parsing and local authentication helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os

from .balance import BalanceResult, QuotaWindow


@dataclass(frozen=True)
class UsageSnapshot:
    """The small, display-safe subset of the Codex usage response."""

    plan_type: str | None
    used_percent: int | None
    remaining_percent: int | None
    reset_after_seconds: int | None
    reset_at: int | None
    limit_window_seconds: int | None
    limit_reached: bool | None
    allowed: bool | None
    credits_balance: str | None
    has_credits: bool | None
    unlimited: bool | None
    overage_limit_reached: bool | None


class UsageError(RuntimeError):
    """Raised when Codex usage cannot be fetched or interpreted."""


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _used_percent(window: Mapping[str, Any] | None) -> int | None:
    if not isinstance(window, Mapping):
        return None
    value = window.get("used_percent")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= 100:
        return None
    return value


def parse_usage_payload(payload: Mapping[str, Any]) -> UsageSnapshot:
    """Parse the JSON returned by ``/backend-api/codex/usage``.

    The endpoint has changed optional fields over time, so missing or null
    fields become ``None`` rather than breaking the tray process.
    """

    if not isinstance(payload, Mapping):
        raise UsageError("Codex usage response is not a JSON object")

    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, Mapping):
        rate_limit = {}
    primary = rate_limit.get("primary_window")
    if not isinstance(primary, Mapping):
        primary = None

    used = _used_percent(primary)
    remaining = None if used is None else 100 - used

    credits = payload.get("credits")
    if not isinstance(credits, Mapping):
        credits = {}
    balance = credits.get("balance")
    if balance is not None:
        balance = str(balance)

    return UsageSnapshot(
        plan_type=str(payload["plan_type"]) if payload.get("plan_type") else None,
        used_percent=used,
        remaining_percent=remaining,
        reset_after_seconds=_number(primary.get("reset_after_seconds")) if primary else None,
        reset_at=_number(primary.get("reset_at")) if primary else None,
        limit_window_seconds=_number(primary.get("limit_window_seconds")) if primary else None,
        limit_reached=rate_limit.get("limit_reached") if isinstance(rate_limit.get("limit_reached"), bool) else None,
        allowed=rate_limit.get("allowed") if isinstance(rate_limit.get("allowed"), bool) else None,
        credits_balance=balance,
        has_credits=credits.get("has_credits") if isinstance(credits.get("has_credits"), bool) else None,
        unlimited=credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None,
        overage_limit_reached=(
            credits.get("overage_limit_reached")
            if isinstance(credits.get("overage_limit_reached"), bool)
            else None
        ),
    )


def default_auth_path() -> Path:
    """Return the Codex auth file path on Windows and compatible systems."""

    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def load_access_credentials(path: Path | None = None) -> tuple[str, str]:
    """Load the access token and account id without ever logging either value."""

    auth_path = path or default_auth_path()
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"無法讀取 Codex 認證檔：{auth_path}") from exc

    if not isinstance(data, Mapping):
        raise UsageError(f"Codex 認證檔格式無效：{auth_path}")
    tokens = data.get("tokens")
    if not isinstance(tokens, Mapping):
        raise UsageError(f"Codex 認證檔缺少有效的 tokens：{auth_path}")
    token = tokens.get("access_token")
    account_id = tokens.get("account_id")

    if not isinstance(token, str) or not token or not isinstance(account_id, str) or not account_id:
        raise UsageError("Codex 認證檔缺少 ChatGPT access token 或 account id")
    return str(token), str(account_id)


__all__ = [
    "BalanceResult",
    "QuotaWindow",
    "UsageError",
    "UsageSnapshot",
    "default_auth_path",
    "load_access_credentials",
    "parse_usage_payload",
]
