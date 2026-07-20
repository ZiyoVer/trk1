"""Product modes and language-pair rules shared by the desktop UI and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageOption:
    code: str
    name: str
    caption: str


@dataclass(frozen=True)
class LanguagePair:
    source: str
    target: str


@dataclass(frozen=True)
class ProductMode:
    code: str
    title: str
    default_pair: LanguagePair


LANGUAGES = (
    LanguageOption("en", "English", "ENGLISH"),
    LanguageOption("uz", "O‘zbekcha", "O‘ZBEKCHA"),
    LanguageOption("ru", "Русский", "РУССКИЙ"),
    LanguageOption("es", "Español", "ESPAÑOL"),
)
AUTO_LANGUAGE = LanguageOption("auto", "Avtomatik", "AVTOMATIK")
SOURCE_LANGUAGES = (AUTO_LANGUAGE, *LANGUAGES)
TARGET_LANGUAGES = LANGUAGES
LANGUAGE_BY_CODE = {language.code: language for language in SOURCE_LANGUAGES}
SOURCE_CODES = frozenset(language.code for language in SOURCE_LANGUAGES)
TARGET_CODES = frozenset(language.code for language in TARGET_LANGUAGES)

PRODUCT_MODES = (
    ProductMode("incoming", "Tinglash", LanguagePair("auto", "uz")),
    ProductMode("outgoing", "Gapirish", LanguagePair("uz", "en")),
)
MODE_BY_CODE = {mode.code: mode for mode in PRODUCT_MODES}
DUPLEX_MODE = ProductMode("duplex", "Ikki tomonlama", LanguagePair("auto", "uz"))
APP_MODES = (*PRODUCT_MODES, DUPLEX_MODE)
APP_MODE_BY_CODE = {mode.code: mode for mode in APP_MODES}


def language_name(code: str) -> str:
    return LANGUAGE_BY_CODE[code].name


def language_caption(code: str) -> str:
    return LANGUAGE_BY_CODE[code].caption


def pair_label(pair: LanguagePair) -> str:
    return f"{language_name(pair.source)}  →  {language_name(pair.target)}"


def duplex_label(incoming: LanguagePair, outgoing: LanguagePair) -> str:
    """Compact summary used by the third, simultaneous product mode."""

    return (
        f"↘ {language_name(incoming.target)}  +  "
        f"↗ {language_name(outgoing.target)}"
    )


def normalize_pair(mode: str, source: str, target: str) -> LanguagePair:
    """Load a valid saved pair without allowing stale settings to break the UI."""

    definition = MODE_BY_CODE.get(mode, PRODUCT_MODES[0])
    source = source.strip().lower()
    target = target.strip().lower()
    if source not in SOURCE_CODES:
        source = definition.default_pair.source
    if target not in TARGET_CODES:
        target = definition.default_pair.target
    if source == target:
        return definition.default_pair
    return LanguagePair(source, target)


def change_source(pair: LanguagePair, source: str) -> LanguagePair:
    """Change source; selecting the current target swaps to a valid pair."""

    source = source.strip().lower()
    if source not in SOURCE_CODES:
        raise ValueError(f"Unsupported source language: {source}")
    if source != pair.target:
        return LanguagePair(source, pair.target)
    if pair.source in TARGET_CODES and pair.source != source:
        return LanguagePair(source, pair.source)
    fallback = next(code for code in ("en", "uz", "ru", "es") if code != source)
    return LanguagePair(source, fallback)


def change_target(pair: LanguagePair, target: str) -> LanguagePair:
    """Change target; selecting the current source swaps to a valid pair."""

    target = target.strip().lower()
    if target not in TARGET_CODES:
        raise ValueError(f"Unsupported target language: {target}")
    if pair.source != target:
        return LanguagePair(pair.source, target)
    return LanguagePair(pair.target, target)


def swap_pair(pair: LanguagePair) -> LanguagePair:
    if pair.source == "auto":
        raise ValueError("Automatic source cannot be used as a target language")
    return LanguagePair(pair.target, pair.source)
