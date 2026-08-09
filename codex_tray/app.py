"""The pystray-based Windows notification-area application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import threading
import webbrowser
from typing import Any

import pystray

from .balance import BalanceResult, CodexBalanceProvider
from .i18n import (
    DEFAULT_LOCALE,
    LocaleStore,
    SUPPORTED_LOCALES,
    language_label,
    normalize_locale,
    translate,
)
from .icon import create_icon_image, set_windows_dpi_awareness
from .monitor import UsageMonitor
from .presentation import format_tooltip


USAGE_URL = "https://chatgpt.com/codex/settings/usage"
DEFAULT_POLL_INTERVAL_SECONDS = 300
INTERVAL_OPTIONS = (
    (60, "interval.60"),
    (300, "interval.300"),
    (900, "interval.900"),
    (1800, "interval.1800"),
)


def _empty_result() -> BalanceResult:
    return BalanceResult(status="loading", source="none")


def _error_status(message: str) -> str:
    lowered = message.lower()
    if any(token in message for token in ("登入", "認證", "token", "auth")) or any(
        token in lowered for token in ("login", "sign in", "signed in", "credential")
    ):
        return "auth_required"
    if any(token in message for token in ("網路", "連線", "timeout", "逾時", "dns")) or any(
        token in lowered for token in ("network", "connection", "unavailable")
    ):
        return "network_error"
    return "service_error"


class TrayApplication:
    """Own the notification icon and connect it to a provider-neutral monitor."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        provider: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        icon_factory: Callable[..., Any] | None = None,
        locale: str | None = None,
        language_path: str | Path | None = None,
        locale_store: LocaleStore | None = None,
    ) -> None:
        # ``client`` remains a compatibility alias for callers of the initial
        # skeleton. New code should pass ``provider``.
        self.provider = (
            provider
            if provider is not None
            else client
            if client is not None
            else CodexBalanceProvider()
        )
        self.client = self.provider
        self.icon_factory = icon_factory or pystray.Icon
        self.locale_store = locale_store or LocaleStore(language_path)
        self.locale = normalize_locale(locale) if locale is not None else self.locale_store.load()
        self.icon: Any | None = None
        self._current_result: Any = _empty_result()
        self._last_good_result: Any | None = None
        self._last_success_at: Any | None = None
        self._render_status: str | None = None
        self._render_error_message: str | None = None
        self._render_last_success_at: Any = None
        self.monitor = UsageMonitor(
            self.provider,
            on_update=self._on_update,
            on_error=self._on_error,
            interval_seconds=poll_interval_seconds,
        )

    @property
    def poll_interval_seconds(self) -> float:
        return self.monitor.interval_seconds

    def set_poll_interval(self, seconds: float) -> None:
        self.monitor.set_interval(seconds)

    def _interval_item(self, seconds: int, label_key: str) -> Any:
        def select(_icon: Any, _item: Any) -> None:
            self.set_poll_interval(seconds)

        def checked(_item: Any) -> bool:
            return self.poll_interval_seconds == seconds

        return pystray.MenuItem(
            translate(label_key, self.locale),
            select,
            checked=checked,
            radio=True,
        )

    def _language_item(self, locale: str) -> Any:
        def select(_icon: Any, _item: Any) -> None:
            self.set_locale(locale)

        def checked(_item: Any) -> bool:
            return self.locale == locale

        return pystray.MenuItem(
            language_label(locale, self.locale),
            select,
            checked=checked,
            radio=True,
        )

    def _build_menu(self) -> Any:
        interval_menu = pystray.Menu(
            *(self._interval_item(seconds, label_key) for seconds, label_key in INTERVAL_OPTIONS)
        )
        language_menu = pystray.Menu(*(self._language_item(locale) for locale in SUPPORTED_LOCALES))
        return pystray.Menu(
            pystray.MenuItem(translate("menu.refresh_now", self.locale), self._manual_refresh),
            pystray.MenuItem(translate("menu.refresh_interval", self.locale), interval_menu),
            pystray.MenuItem(translate("menu.language", self.locale), language_menu),
            pystray.MenuItem(translate("menu.open_details", self.locale), self._open_usage),
            pystray.MenuItem(translate("menu.quit", self.locale), self._quit),
        )

    def _create_icon(self) -> Any:
        if self.icon is not None:
            return self.icon
        self.icon = self.icon_factory(
            "codex-tray",
            create_icon_image(self._current_result),
            format_tooltip(self._current_result, locale=self.locale),
            menu=self._build_menu(),
        )
        return self.icon

    def _render(
        self,
        result: Any,
        *,
        status: str | None = None,
        error_message: str | None = None,
        last_success_at: Any = None,
    ) -> None:
        self._current_result = result
        self._render_status = status
        self._render_error_message = error_message
        self._render_last_success_at = last_success_at
        if self.icon is None:
            return
        self.icon.icon = create_icon_image(result, status=status)
        self.icon.title = format_tooltip(
            result,
            status=status,
            error_message=error_message,
            last_success_at=last_success_at,
            locale=self.locale,
        )

    def set_locale(self, locale: str) -> str:
        """Switch the UI language, persist it, and refresh visible strings."""

        self.locale = normalize_locale(locale)
        self.locale_store.save(self.locale)
        if self.icon is not None:
            self.icon.menu = self._build_menu()
            self._render(
                self._current_result,
                status=self._render_status,
                error_message=self._render_error_message,
                last_success_at=self._render_last_success_at,
            )
        return self.locale

    def _on_update(self, result: Any) -> None:
        self._current_result = result
        result_status = getattr(result, "status", "ok")
        if result_status == "ok":
            self._last_good_result = result
            self._last_success_at = getattr(result, "last_success_at", None) or getattr(
                result, "retrieved_at", None
            )
        elif result_status == "stale" and self._last_good_result is None:
            self._last_good_result = result
        self._render(result)

    def _on_error(self, message: str) -> None:
        stale = self._last_good_result
        status = "stale" if stale is not None else _error_status(message)
        result = stale or self._current_result or _empty_result()
        # The provider normally returns a BalanceResult. ``replace`` keeps the
        # cached value immutable and works around older snapshot objects.
        if stale is not None and isinstance(stale, BalanceResult):
            result = replace(
                stale,
                status="stale",
                error_message=message,
                last_success_at=self._last_success_at,
            )
        self._render(
            result,
            status=status,
            error_message=message,
            last_success_at=self._last_success_at,
        )

    def _manual_refresh(self, _icon: Any, _item: Any) -> None:
        # The monitor owns the worker thread and single-flight guard. The
        # pystray callback therefore returns immediately.
        self.monitor.request_refresh()

    @staticmethod
    def _open_usage(_icon: Any, _item: Any) -> None:
        threading.Thread(
            target=webbrowser.open,
            args=(USAGE_URL,),
            name="codex-open-details",
            daemon=True,
        ).start()

    def _quit(self, icon: Any, _item: Any) -> None:
        self.monitor.stop()
        icon.stop()

    def run(self) -> None:
        set_windows_dpi_awareness()
        icon = self._create_icon()
        self.monitor.start()
        try:
            icon.run()
        finally:
            self.monitor.stop()
