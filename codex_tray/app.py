"""The pystray-based Windows notification-area application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import threading
import webbrowser
from typing import Any

import pystray

from .balance import BalanceResult, CodexBalanceProvider
from .icon import create_icon_image, set_windows_dpi_awareness
from .monitor import UsageMonitor
from .presentation import format_tooltip


USAGE_URL = "https://chatgpt.com/codex/settings/usage"
DEFAULT_POLL_INTERVAL_SECONDS = 300
INTERVAL_OPTIONS = (
    (60, "每 1 分鐘"),
    (300, "每 5 分鐘"),
    (900, "每 15 分鐘"),
    (1800, "每 30 分鐘"),
)


def _empty_result() -> BalanceResult:
    return BalanceResult(status="loading", source="none")


def _error_status(message: str) -> str:
    lowered = message.lower()
    if any(token in message for token in ("登入", "認證", "token", "auth")) or "login" in lowered:
        return "auth_required"
    if any(token in message for token in ("網路", "連線", "timeout", "逾時", "dns")):
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
        self.icon: Any | None = None
        self._current_result: Any = _empty_result()
        self._last_good_result: Any | None = None
        self._last_success_at: Any | None = None
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

    def _interval_item(self, seconds: int, label: str) -> Any:
        def select(_icon: Any, _item: Any) -> None:
            self.set_poll_interval(seconds)

        def checked(_item: Any) -> bool:
            return self.poll_interval_seconds == seconds

        return pystray.MenuItem(label, select, checked=checked, radio=True)

    def _create_icon(self) -> Any:
        if self.icon is not None:
            return self.icon
        interval_menu = pystray.Menu(
            *(self._interval_item(seconds, label) for seconds, label in INTERVAL_OPTIONS)
        )
        menu = pystray.Menu(
            pystray.MenuItem("立即重新整理", self._manual_refresh),
            pystray.MenuItem("更新間隔", interval_menu),
            pystray.MenuItem("開啟詳細資訊", self._open_usage),
            pystray.MenuItem("結束", self._quit),
        )
        self.icon = self.icon_factory(
            "codex-tray",
            create_icon_image(self._current_result),
            "Codex：正在取得額度…",
            menu=menu,
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
        if self.icon is None:
            return
        self.icon.icon = create_icon_image(result, status=status)
        self.icon.title = format_tooltip(
            result,
            status=status,
            error_message=error_message,
            last_success_at=last_success_at,
        )

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
