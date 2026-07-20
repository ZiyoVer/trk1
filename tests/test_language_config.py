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
