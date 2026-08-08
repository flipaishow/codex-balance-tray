"""Non-blocking, single-flight background polling for a balance provider."""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from .presentation import redact_error_message
from .usage import UsageError


class UsageMonitor:
    """Poll a provider without blocking the notification-area event loop.

    ``client`` may expose ``fetch()``, ``get_balance()``, or be a callable.
    This deliberately small boundary keeps the UI independent from the
    provider's transport and from the concrete ``BalanceResult`` class.
    """

    def __init__(
        self,
        client: Any,
        *,
        on_update: Callable[[Any], None],
        on_error: Callable[[str], None],
        interval_seconds: float = 300,
    ) -> None:
        self.client = client
        self.on_update = on_update
        self.on_error = on_error
        self._interval_seconds = max(5, float(interval_seconds))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._refresh_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        # This gate linearizes stop() with the fetch and notification phases.
        # A stop request that wins the gate prevents a refresh from starting;
        # a refresh that already owns it completes before stop() takes effect.
        self._refresh_gate = threading.RLock()
        self._refresh_in_flight = False
        self._generation = 0

    @property
    def interval_seconds(self) -> float:
        with self._state_lock:
            return self._interval_seconds

    def set_interval(self, interval_seconds: float) -> None:
        """Change the polling interval and wake a sleeping loop promptly."""

        try:
            interval = float(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("更新間隔必須是數字") from exc
        if interval < 5:
            raise ValueError("更新間隔不得小於 5 秒")
        with self._state_lock:
            self._interval_seconds = interval
        self._wake.set()

    def _begin_refresh(self) -> bool:
        with self._state_lock:
            if self._refresh_in_flight or self._stop.is_set():
                return False
            self._refresh_in_flight = True
            return True

    def _end_refresh(self) -> None:
        with self._state_lock:
            self._refresh_in_flight = False

    def _fetch(self) -> Any:
        if callable(self.client):
            return self.client()
        fetch = getattr(self.client, "fetch", None)
        if callable(fetch):
            return fetch()
        get_balance = getattr(self.client, "get_balance", None)
        if callable(get_balance):
            return get_balance()
        raise TypeError("餘額資料提供器缺少 fetch() 或 get_balance()")

    def _run_refresh(self, *, acquired: bool = False) -> bool:
        if not acquired and not self._begin_refresh():
            return False
        try:
            with self._refresh_gate:
                with self._state_lock:
                    if self._stop.is_set():
                        return False
                    generation = self._generation
                try:
                    result = self._fetch()
                except UsageError as exc:
                    with self._state_lock:
                        if self._stop.is_set() or generation != self._generation:
                            return False
                        self.on_error(redact_error_message(str(exc)))
                except Exception:
                    # Never expose arbitrary exceptions (which can contain
                    # headers, URLs, or implementation details) through the
                    # notification UI.
                    with self._state_lock:
                        if self._stop.is_set() or generation != self._generation:
                            return False
                        self.on_error("讀取 Codex 餘額時發生未預期錯誤")
                else:
                    with self._state_lock:
                        if self._stop.is_set() or generation != self._generation:
                            return False
                        self.on_update(result)
                return True
        finally:
            self._end_refresh()

    def refresh_once(self) -> bool:
        """Synchronously refresh for tests and controlled callers.

        The tray uses :meth:`request_refresh`; this method is kept synchronous
        as a small, deterministic API for tests and diagnostics.
        """

        return self._run_refresh()

    def _refresh_in_background(self) -> bool:
        return self._run_refresh(acquired=True)

    def request_refresh(self) -> bool:
        """Start a refresh and return immediately.

        Returns ``False`` when another request is already running. This is the
        single-flight guard shared by scheduled and manual refreshes.
        """

        if not self._begin_refresh():
            return False
        try:
            thread = threading.Thread(
                target=self._refresh_in_background,
                name="codex-manual-refresh",
                daemon=True,
            )
            with self._state_lock:
                if self._stop.is_set():
                    self._refresh_in_flight = False
                    return False
                self._refresh_thread = thread
                thread.start()
        except BaseException:
            self._end_refresh()
            raise
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            if self._stop.is_set():
                break
            self.request_refresh()
            self._wake.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._refresh_gate:
            with self._state_lock:
                self._stop.clear()
                self._generation += 1
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="codex-usage-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._refresh_gate:
            with self._state_lock:
                self._stop.set()
                self._generation += 1
            self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        refresh_thread = self._refresh_thread
        if refresh_thread and refresh_thread.is_alive() and refresh_thread is not threading.current_thread():
            refresh_thread.join(timeout=2)
        self._thread = None
        self._refresh_thread = None
