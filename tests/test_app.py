import unittest

from codex_tray.app import TrayApplication
from codex_tray.usage import parse_usage_payload


class FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = ""
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeIconFactory:
    def __init__(self):
        self.icon = FakeIcon()
        self.args = None
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return self.icon


class TrayApplicationTests(unittest.TestCase):
    def _snapshot(self):
        return parse_usage_payload(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 37,
                        "reset_after_seconds": 3600,
                    }
                },
                "credits": {"balance": "0"},
            }
        )

    def test_update_replaces_icon_image_and_tooltip(self):
        factory = FakeIconFactory()
        app = TrayApplication(icon_factory=factory, locale="en")
        app._create_icon()

        app._on_update(self._snapshot())

        self.assertEqual(app.icon.title.splitlines()[0], "Codex 63% remaining · Plus")
        self.assertEqual(app.icon.icon.size, (64, 64))

    def test_error_keeps_tray_alive_and_shows_recovery_hint(self):
        factory = FakeIconFactory()
        app = TrayApplication(icon_factory=factory, locale="en")
        app._create_icon()

        app._on_error("Codex login required")

        self.assertIn("Codex is not signed in", app.icon.title)
        self.assertIn("Codex login is required", app.icon.title)
        self.assertFalse(app.icon.stopped)


if __name__ == "__main__":
    unittest.main()
