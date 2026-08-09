import tempfile
import unittest
from pathlib import Path

from codex_tray.balance import BalanceResult
from codex_tray.i18n import (
    DEFAULT_LOCALE,
    LocaleStore,
    SUPPORTED_LOCALES,
    default_locale_path,
    language_label,
    normalize_locale,
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

    def test_locale_store_persists_all_supported_locales(self):
        expected = ("en", "zh-TW", "zh-CN", "ja-JP")
        self.assertEqual(SUPPORTED_LOCALES, expected)
        with tempfile.TemporaryDirectory() as directory:
            store = LocaleStore(Path(directory) / "settings.json")

            for locale in expected:
                self.assertTrue(store.save(locale))
                self.assertEqual(store.load(), locale)

    def test_locale_aliases_normalize_to_supported_locales(self):
        self.assertEqual(normalize_locale("zh-Hans"), "zh-CN")
        self.assertEqual(normalize_locale("zh-CN"), "zh-CN")
        self.assertEqual(normalize_locale("ja"), "ja-JP")
        self.assertEqual(normalize_locale("ja_JP"), "ja-JP")
        self.assertEqual(language_label("zh-CN", "en"), "Simplified Chinese")
        self.assertEqual(language_label("ja-JP", "ja-JP"), "日本語")

    def test_unknown_locale_falls_back_to_english(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"locale":"fr-FR"}', encoding="utf-8")

            self.assertEqual(LocaleStore(path).load(), "en")
            self.assertEqual(translate("menu.quit", "fr-FR"), "Quit")

    def test_macos_uses_application_support_for_locale_settings(self):
        self.assertEqual(
            default_locale_path(platform="darwin", home=Path("/Users/test-user")),
            Path("/Users/test-user/Library/Application Support/CodexBalanceTray/settings.json"),
        )

    def test_linux_fallback_keeps_xdg_style_locale_settings_path(self):
        self.assertEqual(
            default_locale_path(platform="linux", home=Path("/home/test-user")),
            Path("/home/test-user/.config/codex-balance-tray/settings.json"),
        )

    def test_presentation_supports_all_requested_locales(self):
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
        simplified_chinese = format_tooltip(result, locale="zh-CN")
        japanese = format_tooltip(result, locale="ja-JP")

        self.assertIn("Codex 63% remaining · Plus", english)
        self.assertIn("Reset in: 1 hour 1 minute", english)
        self.assertIn("Codex 剩餘 63% · Plus", traditional_chinese)
        self.assertIn("距離重置：1 小時 1 分鐘", traditional_chinese)
        self.assertIn("Codex 剩余 63% · Plus", simplified_chinese)
        self.assertIn("距离重置：1 小时 1 分钟", simplified_chinese)
        self.assertIn("Codex 残り 63% · Plus", japanese)
        self.assertIn("リセットまで：1 時間 1 分", japanese)

    def test_status_and_error_texts_are_localized(self):
        result = BalanceResult(
            status="auth_required",
            error_message="请先登录 Codex",
        )

        simplified_chinese = format_tooltip(result, locale="zh-CN")
        japanese = format_tooltip(result, locale="ja-JP")

        self.assertIn("尚未登录 Codex", simplified_chinese)
        self.assertIn("需要登录 Codex", simplified_chinese)
        self.assertIn("Codex にログインしていません", japanese)
        self.assertIn("Codex へのログインが必要です", japanese)


if __name__ == "__main__":
    unittest.main()
