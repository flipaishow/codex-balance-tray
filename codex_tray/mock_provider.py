"""Deterministic provider used to exercise the UI without network access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Iterable

from .balance import BalanceResult


class MockBalanceProvider:
    """Return a safe, representative ``BalanceResult`` for UI development.

    A sequence can be supplied to exercise transitions such as success →
    network error → stale success without contacting Codex. The provider has
    no authentication or network side effects.
    """

    def __init__(
        self,
        result: BalanceResult | None = None,
        *,
        results: Iterable[BalanceResult] | None = None,
    ) -> None:
        default = result or BalanceResult(
            status="ok",
            balance=63,
            unit="%",
            plan_type="plus",
            used_percent=37,
            remaining_percent=63,
            reset_after_seconds=7 * 24 * 60 * 60,
            reset_at=int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp()),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            last_success_at=datetime.now(timezone.utc).isoformat(),
            source="mock",
        )
        self._results = iter(results) if results is not None else None
        self._result = default
        self._lock = Lock()
        self.calls = 0

    def fetch(self) -> BalanceResult:
        with self._lock:
            self.calls += 1
            if self._results is not None:
                try:
                    self._result = next(self._results)
                except StopIteration:
                    self._results = None
            return self._result


__all__ = ["MockBalanceProvider"]
