import unittest

from codex_tray.icon import color_for_remaining, create_icon_image
from codex_tray.usage import parse_usage_payload


class IconTests(unittest.TestCase):
    def _snapshot(self, used_percent):
        return parse_usage_payload(
            {
                "plan_type": "plus",
                "rate_limit": {"primary_window": {"used_percent": used_percent}},
                "credits": {"balance": "0"},
            }
        )

    def test_color_changes_with_remaining_budget(self):
        self.assertEqual(color_for_remaining(self._snapshot(90)), "#d13438")
        self.assertEqual(color_for_remaining(self._snapshot(60)), "#ffb900")
        self.assertEqual(color_for_remaining(self._snapshot(20)), "#107c10")

    def test_create_icon_image_has_windows_tray_friendly_size(self):
        image = create_icon_image(self._snapshot(37))

        self.assertEqual(image.size, (64, 64))
        self.assertIn(image.mode, ("RGB", "RGBA"))


if __name__ == "__main__":
    unittest.main()
