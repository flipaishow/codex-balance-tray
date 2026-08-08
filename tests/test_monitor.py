import unittest

from codex_tray.monitor import UsageMonitor
from codex_tray.usage import UsageError, parse_usage_payload


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class UsageMonitorTests(unittest.TestCase):
    def _snapshot(self):
        return parse_usage_payload(
            {
                "plan_type": "plus",
                "rate_limit": {"primary_window": {"used_percent": 25}},
                "credits": {"balance": "0"},
            }
        )

    def test_refresh_once_publishes_latest_snapshot(self):
        updates = []
        errors = []
        client = FakeClient(result=self._snapshot())
        monitor = UsageMonitor(client, on_update=updates.append, on_error=errors.append)

        monitor.refresh_once()

        self.assertEqual(client.calls, 1)
        self.assertEqual(updates, [self._snapshot()])
        self.assertEqual(errors, [])

    def test_refresh_once_keeps_failure_out_of_the_thread(self):
        updates = []
        errors = []
        client = FakeClient(error=UsageError("登入失效"))
        monitor = UsageMonitor(client, on_update=updates.append, on_error=errors.append)

        monitor.refresh_once()

        self.assertEqual(updates, [])
        self.assertEqual(errors, ["登入失效"])


if __name__ == "__main__":
    unittest.main()
