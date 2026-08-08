import threading
import time
import unittest
from codex_tray.balance import BalanceResult
from codex_tray.mock_provider import MockBalanceProvider
from codex_tray.monitor import UsageMonitor
from codex_tray.presentation import format_tooltip, format_tray_title
from codex_tray.usage import UsageError


class BalancePresentationTests(unittest.TestCase):
    def test_unavailable_result_never_looks_like_full_quota(self):
        result = BalanceResult(
            status="auth_required",
            balance=None,
            unit="%",
            retrieved_at=None,
            error_message="請先登入 Codex",
        )

        self.assertEqual(format_tray_title(result), "--")
        tooltip = format_tooltip(result)
        self.assertIn("尚未登入", tooltip)
        self.assertNotIn("100%", tooltip)

    def test_stale_result_is_explicitly_marked_with_last_success_time(self):
        retrieved_at = "2026-08-05T06:00:00+00:00"
        result = BalanceResult(
            status="stale",
            balance=63,
            remaining_percent=63,
            unit="%",
            retrieved_at=retrieved_at,
            last_success_at=retrieved_at,
            error_message="網路暫時無法連線",
        )

        self.assertEqual(format_tray_title(result), "63%*")
        tooltip = format_tooltip(result)
        self.assertIn("資料過期", tooltip)
        self.assertIn("2026", tooltip)
        self.assertIn("網路暫時無法連線", tooltip)

    def test_tooltip_redacts_credentials_from_provider_error(self):
        result = BalanceResult(
            status="network_error",
            error_message="Authorization: Bearer test-token",
        )

        tooltip = format_tooltip(result)

        self.assertNotIn("test-token", tooltip)
        self.assertIn("[REDACTED]", tooltip)

    def test_tooltip_redacts_quoted_and_camel_case_credentials(self):
        result = BalanceResult(
            status="service_error",
            error_message=(
                '{"access_token":"test-token", '
                '"accountId":"test-account", '
                '"Authorization":"Bearer test-bearer"}'
            ),
        )

        tooltip = format_tooltip(result)

        for secret in ("test-token", "test-account", "test-bearer"):
            self.assertNotIn(secret, tooltip)
        self.assertGreaterEqual(tooltip.count("[REDACTED]"), 3)

    def test_tooltip_redacts_unquoted_credential_values_without_echoing_them(self):
        result = BalanceResult(
            status="service_error",
            error_message="access_token=test-token accountId=test-account",
        )

        tooltip = format_tooltip(result)

        self.assertNotIn("test-token", tooltip)
        self.assertNotIn("test-account", tooltip)

    def test_tooltip_redacts_non_bearer_authorization_values(self):
        result = BalanceResult(
            status="service_error",
            error_message='{"Authorization":"Basic test-basic"}',
        )

        tooltip = format_tooltip(result)

        self.assertNotIn("test-basic", tooltip)

    def test_tooltip_redacts_hyphenated_credential_keys(self):
        result = BalanceResult(
            status="service_error",
            error_message=(
                'api-key=test-api-key x-api-key=test-x-api-key '
                'access-token=test-access-token account-id=test-account-id'
            ),
        )

        tooltip = format_tooltip(result)

        for secret in (
            "test-api-key",
            "test-x-api-key",
            "test-access-token",
            "test-account-id",
        ):
            self.assertNotIn(secret, tooltip)

    def test_tooltip_redacts_generic_jwt_shape(self):
        jwt_like = "header-segment-12345.payload-segment-12345.signature-segment-12345"
        result = BalanceResult(status="service_error", error_message=jwt_like)

        tooltip = format_tooltip(result)

        self.assertNotIn(jwt_like, tooltip)
        self.assertIn("[REDACTED]", tooltip)


class MockProviderTests(unittest.TestCase):
    def test_mock_provider_implements_balance_result_contract(self):
        provider = MockBalanceProvider()

        result = provider.fetch()

        self.assertIsInstance(result, BalanceResult)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.unit, "%")
        self.assertIsNotNone(result.remaining_percent)


class MonitorConcurrencyTests(unittest.TestCase):
    def test_monitor_redacts_credentials_from_provider_exception(self):
        errors = []

        def fetch():
            raise UsageError("Authorization: Bearer test-token")

        monitor = UsageMonitor(fetch, on_update=lambda _result: None, on_error=errors.append)

        monitor.refresh_once()

        self.assertNotIn("test-token", errors[0])
        self.assertIn("[REDACTED]", errors[0])

    def test_manual_refresh_is_single_flight_and_non_blocking(self):
        started = threading.Event()
        release = threading.Event()

        def fetch():
            started.set()
            release.wait(2)
            return BalanceResult(status="ok", balance=63, remaining_percent=63, unit="%")

        monitor = UsageMonitor(fetch, on_update=lambda _result: None, on_error=lambda _message: None)
        try:
            begin = time.monotonic()
            self.assertTrue(monitor.request_refresh())
            self.assertLess(time.monotonic() - begin, 0.25)
            self.assertTrue(started.wait(1))
            self.assertFalse(monitor.request_refresh())
        finally:
            release.set()
            monitor.stop()

    def test_stop_serializes_with_inflight_fetch_before_callback(self):
        fetch_started = threading.Event()
        release_fetch = threading.Event()
        stop_returned = threading.Event()
        updates = []

        def fetch():
            fetch_started.set()
            release_fetch.wait(1)
            return BalanceResult(status="ok", balance=63, remaining_percent=63, unit="%")

        monitor = UsageMonitor(fetch, on_update=updates.append, on_error=lambda _message: None)
        self.assertTrue(monitor.request_refresh())
        self.assertTrue(fetch_started.wait(1))

        stopper = threading.Thread(target=lambda: (monitor.stop(), stop_returned.set()))
        stopper.start()
        try:
            self.assertFalse(stop_returned.wait(0.05))
        finally:
            release_fetch.set()
            stopper.join(1)

        self.assertTrue(stop_returned.is_set())
        self.assertEqual(len(updates), 1)

    def test_refresh_does_not_start_after_stop_wins_the_race(self):
        calls = []
        monitor = UsageMonitor(
            lambda: calls.append("fetch"),
            on_update=lambda _result: None,
            on_error=lambda _message: None,
        )

        self.assertTrue(monitor._begin_refresh())
        monitor._stop.set()
        monitor._refresh_in_background()

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
