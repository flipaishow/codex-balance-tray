import tempfile
import unittest
from pathlib import Path

from codex_tray.balance import BalanceResult
from codex_tray.i18n import (
    DEFAULT_LOCALE,
    LocaleStore,
    SUPPORTED_LOCALES,
    translate,
)
from codex_tray.presentation import format_tooltip


class InternationalizationTests(unittest.TestCase):
    def test_fresh_locale_store_defaults_to_english(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocaleStore(Path(directory) / "settings.json")

            self.assertEqual(store.load(), DEFAULT_LOCALE)
            self.assertEqual(DEFAULT_LOCALE, "en")
            self.assertEqual(translate("menu.quit"), "Quit")

    def test_locale_store_persists_supported_locale(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocaleStore(Path(directory) / "settings.json")

            self.assertTrue(store.save("zh-TW"))
            self.assertEqual(store.load(), "zh-TW")
            self.assertIn("en", SUPPORTED_LOCALES)
            self.assertIn("zh-TW", SUPPORTED_LOCALES)

    def test_unknown_locale_falls_back_to_english(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"locale":"fr-FR"}', encoding="utf-8")

            self.assertEqual(LocaleStore(path).load(), "en")
            self.assertEqual(translate("menu.quit", "fr-FR"), "Quit")

    def test_presentation_defaults_to_english_and_supports_traditional_chinese(self):
        result = BalanceResult(
            status="ok",
            balance=63,
            remaining_percent=63,
            unit="%",
            plan_type="plus",
            reset_after_seconds=3661,
        )

        english = format_tooltip(result)
        traditional_chinese = format_tooltip(result, locale="zh-TW")

        self.assertIn("Codex 63% remaining · Plus", english)
        self.assertIn("Reset in: 1 hour 1 minute", english)
        self.assertIn("Codex 剩餘 63% · Plus", traditional_chinese)
        self.assertIn("距離重置：1 小時 1 分鐘", traditional_chinese)


if __name__ == "__main__":
    unittest.main()
