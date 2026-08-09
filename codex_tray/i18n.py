"""Small, dependency-free localization support for the tray UI.

The application intentionally defaults to English instead of inheriting the
host operating system locale. A user can switch to Traditional Chinese from
the tray menu; the selected locale is persisted in a non-secret JSON settings
file so the choice survives restart.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "zh-TW")
LANGUAGE_LABELS = {
    "en": {"en": "English", "zh-TW": "英文"},
    "zh-TW": {"en": "Traditional Chinese", "zh-TW": "繁體中文"},
}

_MESSAGES: dict[str, dict[str, str]] = {
    "menu.refresh_now": {"en": "Refresh now", "zh-TW": "立即重新整理"},
    "menu.refresh_interval": {"en": "Refresh interval", "zh-TW": "更新間隔"},
    "menu.open_details": {"en": "Open details", "zh-TW": "開啟詳細資訊"},
    "menu.language": {"en": "Language", "zh-TW": "語言"},
    "menu.quit": {"en": "Quit", "zh-TW": "結束"},
    "interval.60": {"en": "Every 1 minute", "zh-TW": "每 1 分鐘"},
    "interval.300": {"en": "Every 5 minutes", "zh-TW": "每 5 分鐘"},
    "interval.900": {"en": "Every 15 minutes", "zh-TW": "每 15 分鐘"},
    "interval.1800": {"en": "Every 30 minutes", "zh-TW": "每 30 分鐘"},
    "status.loading": {"en": "Loading Codex quota…", "zh-TW": "正在取得 Codex 額度資料…"},
    "status.auth_required": {"en": "Codex is not signed in", "zh-TW": "尚未登入 Codex"},
    "status.unsupported_auth": {
        "en": "This login method has no ChatGPT Codex quota data",
        "zh-TW": "目前登入方式沒有 ChatGPT Codex 額度資料",
    },
    "status.unsupported_route": {
        "en": "Codex version/endpoint is incompatible",
        "zh-TW": "Codex 版本／endpoint 不相容",
    },
    "status.network_error": {
        "en": "Network unavailable",
        "zh-TW": "網路暫時無法取得 Codex 額度",
    },
    "status.rate_limited": {
        "en": "Codex service is rate-limiting requests",
        "zh-TW": "Codex 服務暫時限流",
    },
    "status.cli_unavailable": {"en": "Codex CLI not found", "zh-TW": "找不到 Codex CLI"},
    "status.schema_changed": {
        "en": "Codex response format changed",
        "zh-TW": "Codex 回應格式已變更",
    },
    "status.service_error": {
        "en": "Service unavailable",
        "zh-TW": "Codex 服務暫時無法提供額度",
    },
    "status.unavailable": {
        "en": "No quota data is currently available",
        "zh-TW": "目前沒有可用的額度資料",
    },
    "plan.unknown": {"en": "Unknown plan", "zh-TW": "未知方案"},
    "remaining.unlimited": {"en": "Unlimited", "zh-TW": "無上限"},
    "duration.unknown": {"en": "Unknown", "zh-TW": "未知"},
    "duration.resetting_soon": {"en": "Resetting soon", "zh-TW": "即將重置"},
    "duration.less_than_minute": {"en": "less than 1 minute", "zh-TW": "不到 1 分鐘"},
    "tooltip.remaining": {
        "en": "Codex {remaining} remaining · {plan}",
        "zh-TW": "Codex 剩餘 {remaining} · {plan}",
    },
    "tooltip.quota_status": {
        "en": "Codex quota: {status}",
        "zh-TW": "Codex 額度：{status}",
    },
    "tooltip.stale": {
        "en": "Stale data (last success)",
        "zh-TW": "資料過期（顯示上次成功資料）",
    },
    "tooltip.usage": {"en": "Usage: {used}{unit}", "zh-TW": "使用率：{used}{unit}"},
    "tooltip.reset_after": {"en": "Reset in: {duration}", "zh-TW": "距離重置：{duration}"},
    "tooltip.reset_at": {"en": "Reset time: {time}", "zh-TW": "重置時間：{time}"},
    "tooltip.credits": {"en": "Credits balance: {credits}", "zh-TW": "Credits 餘額：{credits}"},
    "tooltip.overage": {"en": "Overage limit reached", "zh-TW": "已達超額上限"},
    "tooltip.last_success": {
        "en": "Last success: {time}",
        "zh-TW": "上次成功：{time}",
    },
    "tooltip.reason": {"en": "Reason: {message}", "zh-TW": "原因：{message}"},
    "forecast.average": {
        "en": "Average usage: {value}%/day",
        "zh-TW": "平均消耗：{value}%／天",
    },
    "forecast.exhaustion": {
        "en": "Estimated exhaustion in {duration}",
        "zh-TW": "預估 {duration}後用完",
    },
    "forecast.no_usage": {
        "en": "Estimated exhaustion: no usage observed",
        "zh-TW": "預估耗盡：目前未觀測到消耗",
    },
    "forecast.at_risk": {
        "en": "Before reset: may run out early (about {value}% over)",
        "zh-TW": "重置前：可能提前用完（約超出 {value}%）",
    },
    "forecast.judgment_risk": {
        "en": "Assessment: may run out before reset",
        "zh-TW": "判斷：可能在 reset 前用完",
    },
    "forecast.projected": {
        "en": "Before reset: about {value}% remaining",
        "zh-TW": "重置前：約 {value}%",
    },
    "forecast.judgment_no_usage": {
        "en": "Assessment: no usage observed yet",
        "zh-TW": "判斷：目前未觀測到消耗",
    },
    "forecast.judgment_on_track": {
        "en": "Assessment: current rate should last until reset",
        "zh-TW": "判斷：照目前速度可撐到 reset",
    },
    "forecast.compact_rate": {
        "en": "Avg {value}%/d · runout {duration}",
        "zh-TW": "平均消耗：{value}%／天；預估 {duration}後用完",
    },
    "forecast.compact_rate_no_usage": {
        "en": "Avg {value}%/d · no usage observed",
        "zh-TW": "平均消耗：{value}%／天；目前未觀測到消耗",
    },
    "forecast.compact_reset": {
        "en": "At reset: ~{value}% left · on track",
        "zh-TW": "重置前：約 {value}%；照目前速度可撐到 reset",
    },
    "forecast.compact_reset_no_usage": {
        "en": "At reset: ~{value}% left · no usage observed",
        "zh-TW": "重置前：約 {value}%；目前未觀測到消耗",
    },
    "forecast.compact_risk": {
        "en": "Risk: runout before reset",
        "zh-TW": "判斷：可能在 reset 前用完",
    },
    "error.auth_required": {
        "en": "Codex login is required",
        "zh-TW": "Codex 尚未登入或登入已失效，請先重新登入",
    },
    "error.unsupported_auth": {
        "en": "The current login method has no Codex quota data",
        "zh-TW": "目前登入方式沒有 ChatGPT Codex 額度資料",
    },
    "error.cli_unavailable": {
        "en": "Codex CLI is unavailable",
        "zh-TW": "Codex app-server 無法使用",
    },
    "error.network_error": {
        "en": "Network unavailable",
        "zh-TW": "網路暫時無法取得 Codex 額度",
    },
    "error.rate_limited": {
        "en": "Codex service asked the app to retry later",
        "zh-TW": "Codex 服務要求稍後重試",
    },
    "error.unsupported_route": {
        "en": "The Codex quota endpoint is incompatible",
        "zh-TW": "Codex 額度 endpoint 不相容",
    },
    "error.schema_changed": {
        "en": "The Codex response format changed and could not be parsed",
        "zh-TW": "Codex 回應格式已變更，暫時無法解析額度資料",
    },
    "error.service_error": {
        "en": "Service unavailable",
        "zh-TW": "Codex 服務暫時無法提供額度資料",
    },
}

_STATUS_ERROR_KEYS = {
    "auth_required": "error.auth_required",
    "unsupported_auth": "error.unsupported_auth",
    "cli_unavailable": "error.cli_unavailable",
    "network_error": "error.network_error",
    "rate_limited": "error.rate_limited",
    "unsupported_route": "error.unsupported_route",
    "schema_changed": "error.schema_changed",
    "service_error": "error.service_error",
}
_ERROR_CODE_KEYS = {
    "AUTH_REQUIRED": "error.auth_required",
    "UNSUPPORTED_AUTH": "error.unsupported_auth",
    "CLI_NOT_FOUND": "error.cli_unavailable",
    "NETWORK_ERROR": "error.network_error",
    "RATE_LIMITED": "error.rate_limited",
    "UNSUPPORTED_ROUTE": "error.unsupported_route",
    "SCHEMA_CHANGED": "error.schema_changed",
    "PROVIDER_ERROR": "error.service_error",
    "INVALID_PROVIDER_RESULT": "error.service_error",
    "SERVICE_UNAVAILABLE": "error.service_error",
}
_ERROR_MESSAGE_ALIASES = {
    "請先登入 Codex": "error.auth_required",
    "網路暫時無法連線": "error.network_error",
    "Network temporarily unavailable": "error.network_error",
    "讀取 Codex 餘額時發生未預期錯誤": "error.service_error",
}


def normalize_locale(value: Any) -> str:
    """Return a supported locale, defaulting to English for unknown values."""

    if not isinstance(value, str):
        return DEFAULT_LOCALE
    normalized = value.strip().replace("_", "-").lower()
    if normalized in {"zh", "zh-tw", "zh-hant", "zh-hant-tw"}:
        return "zh-TW"
    if normalized == "en" or normalized.startswith("en-"):
        return "en"
    return DEFAULT_LOCALE


def translate(key: str, locale: Any = DEFAULT_LOCALE, **values: Any) -> str:
    """Translate a key and safely format its named values."""

    messages = _MESSAGES.get(key)
    if messages is None:
        return key
    selected = normalize_locale(locale)
    template = messages.get(selected) or messages[DEFAULT_LOCALE]
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


def language_label(locale: Any, selected_locale: Any = DEFAULT_LOCALE) -> str:
    """Return the name of ``locale`` in the currently selected language."""

    target = normalize_locale(locale)
    selected = normalize_locale(selected_locale)
    return LANGUAGE_LABELS[target][selected]


def localized_error_key(
    *,
    status: Any = None,
    error_code: Any = None,
    message: Any = None,
) -> str | None:
    """Map safe provider classifications and known legacy messages to a key."""

    code = str(error_code).upper() if error_code else ""
    key = _ERROR_CODE_KEYS.get(code)
    if key:
        return key
    if code.startswith("HTTP_"):
        suffix = code.removeprefix("HTTP_")
        if suffix in {"401", "403"}:
            return "error.auth_required"
        if suffix == "429":
            return "error.rate_limited"
        if suffix in {"404", "405"}:
            return "error.unsupported_route"
        if suffix.isdigit() and int(suffix) >= 500:
            return "error.service_error"
    if isinstance(message, str):
        key = _ERROR_MESSAGE_ALIASES.get(message.strip())
        if key:
            return key
    return _STATUS_ERROR_KEYS.get(str(status))


def default_locale_path() -> Path:
    """Return the non-secret settings path used by the tray application."""

    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "CodexBalanceTray" / "settings.json"
    return Path.home() / ".config" / "codex-balance-tray" / "settings.json"


class LocaleStore:
    """Persist only the selected locale in a small JSON settings file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_locale_path()

    def load(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return DEFAULT_LOCALE
        if not isinstance(data, Mapping):
            return DEFAULT_LOCALE
        return normalize_locale(data.get("locale"))

    def save(self, locale: Any) -> bool:
        selected = normalize_locale(locale)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"locale": selected}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return False
        return True


__all__ = [
    "DEFAULT_LOCALE",
    "LANGUAGE_LABELS",
    "LocaleStore",
    "SUPPORTED_LOCALES",
    "default_locale_path",
    "language_label",
    "localized_error_key",
    "normalize_locale",
    "translate",
]
