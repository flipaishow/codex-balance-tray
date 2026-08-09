import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_tray.app import TrayApplication
from codex_tray.balance import BalanceResult
from codex_tray.mock_provider import MockBalanceProvider


class _FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = ""
        self.menu = None
        self.stopped = False

    def update_menu(self):
        return None

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
            locale="en",
        )

        app._create_icon()

        menu = factory.kwargs["menu"]
        labels = [item.text for item in menu]
        self.assertIn("Refresh now", labels)
        self.assertIn("Refresh interval", labels)
        self.assertIn("Open details", labels)
        self.assertIn("Language", labels)
        self.assertIn("Quit", labels)

    def test_language_menu_defaults_to_english_and_switches_to_traditional_chinese(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = _FakeIconFactory()
            app = TrayApplication(
                provider=MockBalanceProvider(),
                icon_factory=factory,
                language_path=Path(directory) / "settings.json",
                locale="en",
            )
            app._create_icon()
            app._on_update(
                BalanceResult(
                    status="ok",
                    balance=63,
                    remaining_percent=63,
                    unit="%",
                    plan_type="plus",
                )
            )

            self.assertEqual(app.locale, "en")
            self.assertIn("Codex 63% remaining", factory.icon.title)

            app.set_locale("zh-TW")

            labels = [item.text for item in factory.icon.menu]
            self.assertEqual(app.locale, "zh-TW")
            self.assertIn("立即重新整理", labels)
            self.assertIn("語言", labels)
            self.assertIn("Codex 剩餘 63%", factory.icon.title)

    def test_language_menu_exposes_all_four_supported_locales(self):
        factory = _FakeIconFactory()
        app = TrayApplication(provider=MockBalanceProvider(), icon_factory=factory, locale="en")

        app._create_icon()

        language_item = next(item for item in factory.kwargs["menu"] if item.text == "Language")
        labels = [item.text for item in language_item.submenu]
        self.assertEqual(
            labels,
            ["English", "Traditional Chinese", "Simplified Chinese", "Japanese"],
        )

    def test_setting_interval_updates_monitor_without_fetching(self):
        provider = MockBalanceProvider()
        app = TrayApplication(provider=provider, icon_factory=_FakeIconFactory(), locale="en")

        app.set_poll_interval(600)

        self.assertEqual(app.poll_interval_seconds, 600)
        self.assertEqual(app.monitor.interval_seconds, 600)
        self.assertEqual(provider.calls, 0)

    def test_error_state_is_explicit_and_preserves_last_good_value(self):
        factory = _FakeIconFactory()
        app = TrayApplication(provider=MockBalanceProvider(), icon_factory=factory, locale="en")
        app._create_icon()
        app._on_update(
            BalanceResult(
                status="ok",
                balance=63,
                remaining_percent=63,
                unit="%",
            )
        )

        app._on_error("Network temporarily unavailable")

        self.assertIn("63%*", app.icon.title)
        self.assertIn("Stale data", app.icon.title)
        self.assertIn("Network unavailable", app.icon.title)

    def test_manual_refresh_does_not_run_provider_on_tray_callback(self):
        provider = MockBalanceProvider()
        app = TrayApplication(provider=provider, icon_factory=_FakeIconFactory(), locale="en")
        app._create_icon()

        with patch.object(app.monitor, "request_refresh", return_value=True) as refresh:
            app._manual_refresh(None, None)

        refresh.assert_called_once_with()
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
