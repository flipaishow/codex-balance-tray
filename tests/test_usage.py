import json
import tempfile
import unittest
from pathlib import Path

from codex_tray.usage import UsageError, UsageSnapshot, load_access_credentials, parse_usage_payload
from codex_tray.presentation import format_tray_title, format_tooltip


class UsageParsingTests(unittest.TestCase):
    def test_parses_remaining_percent_from_primary_window(self):
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": {
                    "used_percent": 37,
                    "limit_window_seconds": 604800,
                    "reset_after_seconds": 271722,
                    "reset_at": 1786160194,
                },
                "secondary_window": None,
            },
            "credits": {
                "has_credits": False,
                "unlimited": False,
                "overage_limit_reached": False,
                "balance": "0",
            },
        }

        snapshot = parse_usage_payload(payload)

        self.assertIsInstance(snapshot, UsageSnapshot)
        self.assertEqual(snapshot.used_percent, 37)
        self.assertEqual(snapshot.remaining_percent, 63)
        self.assertEqual(snapshot.plan_type, "plus")
        self.assertEqual(snapshot.reset_after_seconds, 271722)
        self.assertEqual(snapshot.credits_balance, "0")

    def test_missing_rate_limit_is_reported_as_unavailable(self):
        snapshot = parse_usage_payload({"plan_type": "plus", "credits": None})

        self.assertIsNone(snapshot.used_percent)
        self.assertIsNone(snapshot.remaining_percent)
        self.assertEqual(snapshot.plan_type, "plus")
        self.assertIsNone(snapshot.reset_after_seconds)

    def test_invalid_used_percent_is_not_clamped_to_a_fake_balance(self):
        snapshot = parse_usage_payload(
            {
                "plan_type": "plus",
                "rate_limit": {"primary_window": {"used_percent": "37"}},
            }
        )

        self.assertIsNone(snapshot.used_percent)
        self.assertIsNone(snapshot.remaining_percent)

    def test_malformed_non_object_auth_file_is_safe_usage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

            with self.assertRaises(UsageError) as context:
                load_access_credentials(path)

        self.assertIn("認證檔", str(context.exception))
        self.assertNotIn("AttributeError", str(context.exception))


class PresentationTests(unittest.TestCase):
    def test_tray_title_contains_remaining_percentage(self):
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {"used_percent": 37, "reset_after_seconds": 3600}
            },
            "credits": {"balance": "0"},
        }

        snapshot = parse_usage_payload(payload)

        self.assertEqual(format_tray_title(snapshot), "63%")

    def test_tooltip_explains_plan_and_reset(self):
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {"used_percent": 37, "reset_after_seconds": 3661}
            },
            "credits": {"balance": "0"},
        }

        tooltip = format_tooltip(parse_usage_payload(payload))

        self.assertIn("Codex", tooltip)
        self.assertIn("63%", tooltip)
        self.assertIn("Plus", tooltip)
        self.assertIn("Reset in: 1 hour 1 minute", tooltip)


if __name__ == "__main__":
    unittest.main()
