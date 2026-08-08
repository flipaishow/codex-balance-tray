import unittest
from unittest.mock import patch

from codex_tray.app import TrayApplication
from codex_tray.balance import BalanceResult
from codex_tray.mock_provider import MockBalanceProvider


class _FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = ""
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeIconFactory:
    def __init__(self):
        self.icon = _FakeIcon()
        self.args = ()
        self.kwargs = {}

    def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return self.icon


class TrayMenuTests(unittest.TestCase):
    def test_menu_exposes_refresh_interval_details_and_exit(self):
        factory = _FakeIconFactory()
        app = TrayApplication(
            provider=MockBalanceProvider(),
            icon_factory=factory,
            poll_interval_seconds=300,
        )

        app._create_icon()

        menu = factory.kwargs["menu"]
        labels = [item.text for item in menu]
        self.assertIn("立即重新整理", labels)
        self.assertIn("更新間隔", labels)
        self.assertIn("開啟詳細資訊", labels)
        self.assertIn("結束", labels)

    def test_setting_interval_updates_monitor_without_fetching(self):
        provider = MockBalanceProvider()
        app = TrayApplication(provider=provider, icon_factory=_FakeIconFactory())

        app.set_poll_interval(600)

        self.assertEqual(app.poll_interval_seconds, 600)
        self.assertEqual(app.monitor.interval_seconds, 600)
        self.assertEqual(provider.calls, 0)

    def test_error_state_is_explicit_and_preserves_last_good_value(self):
        factory = _FakeIconFactory()
        app = TrayApplication(provider=MockBalanceProvider(), icon_factory=factory)
        app._create_icon()
        app._on_update(
            BalanceResult(
                status="ok",
                balance=63,
                remaining_percent=63,
                unit="%",
            )
        )

        app._on_error("網路暫時無法連線")

        self.assertIn("63%*", app.icon.title)
        self.assertIn("資料過期", app.icon.title)
        self.assertIn("網路暫時無法連線", app.icon.title)

    def test_manual_refresh_does_not_run_provider_on_tray_callback(self):
        provider = MockBalanceProvider()
        app = TrayApplication(provider=provider, icon_factory=_FakeIconFactory())
        app._create_icon()

        with patch.object(app.monitor, "request_refresh", return_value=True) as refresh:
            app._manual_refresh(None, None)

        refresh.assert_called_once_with()
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
