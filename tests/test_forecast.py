import unittest

from codex_tray.forecast import calculate_usage_forecast
from codex_tray.presentation import format_tooltip
from codex_tray.usage import parse_usage_payload


class UsageForecastTests(unittest.TestCase):
    def _snapshot(self, *, used: int, reset_after: int, window: int = 7 * 24 * 60 * 60):
        return parse_usage_payload(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": used,
                        "limit_window_seconds": window,
                        "reset_after_seconds": reset_after,
                    }
                },
                "credits": {"balance": "0"},
            }
        )

    def test_on_track_forecast_reports_rate_exhaustion_and_reset_remaining(self):
        snapshot = self._snapshot(used=37, reset_after=3 * 24 * 60 * 60)

        forecast = calculate_usage_forecast(snapshot)
        tooltip = format_tooltip(snapshot)

        self.assertEqual(forecast.status, "on_track")
        self.assertAlmostEqual(forecast.average_daily_percent or 0, 9.25, places=2)
        self.assertAlmostEqual(forecast.projected_remaining_percent or 0, 35.25, places=2)
        self.assertIn("Avg 9.3%/d · runout 6 days 19 hours", tooltip)
        self.assertIn("At reset: ~35.3% left · on track", tooltip)
        self.assertLessEqual(len(tooltip), 128)

    def test_at_risk_forecast_warns_before_reset(self):
        snapshot = self._snapshot(used=80, reset_after=4 * 24 * 60 * 60)

        forecast = calculate_usage_forecast(snapshot)
        tooltip = format_tooltip(snapshot)

        self.assertEqual(forecast.status, "at_risk")
        self.assertLess(forecast.estimated_exhaustion_seconds or 0, forecast.reset_after_seconds or 0)
        self.assertLess(forecast.projected_remaining_percent or 0, 0)
        self.assertIn("Risk: runout before reset", tooltip)
        self.assertIn("runout 18 hours", tooltip)

    def test_short_observation_window_is_marked_insufficient(self):
        snapshot = self._snapshot(used=10, reset_after=7 * 24 * 60 * 60 - 30 * 60)

        forecast = calculate_usage_forecast(snapshot)
        tooltip = format_tooltip(snapshot)

        self.assertEqual(forecast.status, "insufficient_data")
        self.assertIsNone(forecast.average_daily_percent)
        self.assertNotIn("Avg ", tooltip)
        self.assertNotIn("Risk: runout before reset", tooltip)

    def test_zero_usage_does_not_invent_an_exhaustion_time(self):
        snapshot = self._snapshot(used=0, reset_after=3 * 24 * 60 * 60)

        forecast = calculate_usage_forecast(snapshot)
        tooltip = format_tooltip(snapshot)

        self.assertEqual(forecast.status, "no_usage")
        self.assertEqual(forecast.average_daily_percent, 0)
        self.assertIsNone(forecast.estimated_exhaustion_seconds)
        self.assertIn("Avg 0%/d · no usage observed", tooltip)
        self.assertIn("At reset: ~100% left · no usage observed", tooltip)


if __name__ == "__main__":
    unittest.main()
