import unittest

from language_config import (
    LanguagePair,
    change_source,
    change_target,
    normalize_pair,
    pair_label,
    swap_pair,
)


class LanguageConfigTests(unittest.TestCase):
    def test_incoming_defaults_to_automatic_source_and_uzbek_target(self) -> None:
        pair = normalize_pair("incoming", "", "")
        self.assertEqual(pair, LanguagePair("auto", "uz"))
        self.assertEqual(pair_label(pair), "Avtomatik  →  O‘zbekcha")

    def test_each_mode_keeps_a_valid_independent_pair(self) -> None:
        incoming = normalize_pair("incoming", "ru", "uz")
        outgoing = normalize_pair("outgoing", "uz", "en")
        self.assertEqual(incoming, LanguagePair("ru", "uz"))
        self.assertEqual(outgoing, LanguagePair("uz", "en"))

    def test_selecting_the_other_side_swaps_instead_of_matching(self) -> None:
        pair = LanguagePair("ru", "uz")
        self.assertEqual(change_source(pair, "uz"), LanguagePair("uz", "ru"))
        self.assertEqual(change_target(pair, "ru"), LanguagePair("uz", "ru"))

    def test_explicit_pair_can_be_swapped(self) -> None:
        self.assertEqual(
            swap_pair(LanguagePair("uz", "en")), LanguagePair("en", "uz")
        )
        with self.assertRaises(ValueError):
            swap_pair(LanguagePair("auto", "uz"))

    def test_spanish_is_available_in_both_directions(self) -> None:
        self.assertEqual(normalize_pair("incoming", "es", "uz"), LanguagePair("es", "uz"))
        self.assertEqual(normalize_pair("outgoing", "uz", "es"), LanguagePair("uz", "es"))


if __name__ == "__main__":
    unittest.main()


class UiI18nTests(unittest.TestCase):
    def setUp(self) -> None:
        import ui_i18n
        self.ui_i18n = ui_i18n
        self._prev = ui_i18n.current_language()

    def tearDown(self) -> None:
        self.ui_i18n.set_language(self._prev)

    def test_uzbek_source_passes_through(self) -> None:
        self.ui_i18n.set_language("uz")
        self.assertEqual(self.ui_i18n.t("Tinglash"), "Tinglash")

    def test_russian_and_english(self) -> None:
        self.ui_i18n.set_language("ru")
        self.assertEqual(self.ui_i18n.t("Tinglash"), "Слушать")
        self.ui_i18n.set_language("en")
        self.assertEqual(self.ui_i18n.t("Tinglash"), "Listen")

    def test_unknown_string_falls_back_to_source(self) -> None:
        self.ui_i18n.set_language("ru")
        self.assertEqual(self.ui_i18n.t("__yo'q__"), "__yo'q__")

    def test_format_args(self) -> None:
        self.ui_i18n.set_language("en")
        self.assertEqual(self.ui_i18n.t("Manba tili: {}", "UZ"), "Source language: UZ")
