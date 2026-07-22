"""Interfeys tili: o'zbek (asl), rus, ingliz.

Yondashuv: o'zbekcha satr — kalit. `t("O'zbekcha matn")` joriy tilga
o'giradi; lug'atda bo'lmasa yoki til=uz bo'lsa o'zini qaytaradi. Shu
sabab satrlarni bosqichma-bosqich o'rashda hech narsa buzilmaydi.
"""

from __future__ import annotations

import ctypes
import locale
import platform

SUPPORTED = ("uz", "ru", "en")
LANGUAGE_NAMES = {"uz": "O‘zbekcha", "ru": "Русский", "en": "English"}

_current = "uz"


# Kalit = o'zbekcha manba satri; qiymat = {ru, en}.
_STRINGS: dict[str, dict[str, str]] = {
    # --- Menyu paneli ---
    "Tayyor": {"ru": "Готово", "en": "Ready"},
    "Oynani ko‘rsatish": {"ru": "Показать окно", "en": "Show window"},
    "Tinglash": {"ru": "Слушать", "en": "Listen"},
    "Gapirish": {"ru": "Говорить", "en": "Speak"},
    "Ikki tomonlama": {"ru": "Двусторонний", "en": "Two-way"},
    "Tarjimani boshlash": {"ru": "Начать перевод", "en": "Start translation"},
    "Tarjimani to‘xtatish": {"ru": "Остановить перевод", "en": "Stop translation"},
    "Tarjimani o‘zim ham eshitay": {
        "ru": "Слышать перевод самому",
        "en": "Hear translation myself",
    },
    "Loglarni yig‘ish (ZIP)": {"ru": "Собрать логи (ZIP)", "en": "Collect logs (ZIP)"},
    "Tizim mikrofonini tiklash": {
        "ru": "Восстановить системный микрофон",
        "en": "Restore system microphone",
    },
    "Sozlamalar…": {"ru": "Настройки…", "en": "Settings…"},
    "Sozlamalar": {"ru": "Настройки", "en": "Settings"},
    "Chiqish": {"ru": "Выход", "en": "Quit"},
    "Manba tili": {"ru": "Язык источника", "en": "Source language"},
    "Tarjima tili": {"ru": "Язык перевода", "en": "Target language"},
    "Interfeys tili": {"ru": "Язык интерфейса", "en": "Interface language"},
    "Manba tili: {}": {"ru": "Язык источника: {}", "en": "Source language: {}"},
    "Tarjima tili: {}": {"ru": "Язык перевода: {}", "en": "Target language: {}"},
    # --- Statuslar ---
    "TAYYOR": {"ru": "ГОТОВО", "en": "READY"},
    "ULANMOQDA…": {"ru": "ПОДКЛЮЧЕНИЕ…", "en": "CONNECTING…"},
    "TARJIMA ISHLAYAPTI": {"ru": "ПЕРЕВОД РАБОТАЕТ", "en": "TRANSLATING"},
    "IKKALA TARJIMA ISHLAYAPTI": {
        "ru": "ОБА ПЕРЕВОДА РАБОТАЮТ",
        "en": "BOTH TRANSLATIONS RUNNING",
    },
    "TO‘XTATILMOQDA…": {"ru": "ОСТАНОВКА…", "en": "STOPPING…"},
    "TO‘XTADI": {"ru": "ОСТАНОВЛЕНО", "en": "STOPPED"},
    "QAYTA ULANMOQDA…": {"ru": "ПЕРЕПОДКЛЮЧЕНИЕ…", "en": "RECONNECTING…"},
    "API KEY KERAK": {"ru": "НУЖЕН API-КЛЮЧ", "en": "API KEY REQUIRED"},
    "AUDIO QURILMA TANLANG": {
        "ru": "ВЫБЕРИТЕ АУДИОУСТРОЙСТВО",
        "en": "SELECT AUDIO DEVICE",
    },
    "LOGLAR SAQLANDI": {"ru": "ЛОГИ СОХРАНЕНЫ", "en": "LOGS SAVED"},
    # --- Bildirishnomalar ---
    "Ishga tushdi — yuqoridagi belgidan boshqaring.": {
        "ru": "Запущено — управляйте из значка вверху.",
        "en": "Running — control it from the icon above.",
    },
    "Oyna yashirildi — menyu panelidagi belgidan qaytariladi.": {
        "ru": "Окно скрыто — вернуть можно значком в строке меню.",
        "en": "Window hidden — restore it from the menu-bar icon.",
    },
    "Keyingi ishga tushirishda qo‘llanadi.": {
        "ru": "Применится при следующем запуске.",
        "en": "Will apply on next start.",
    },
    "Rejimni almashtirish uchun avval tarjimani to‘xtating.": {
        "ru": "Чтобы сменить режим, сначала остановите перевод.",
        "en": "Stop the translation first to change the mode.",
    },
}


def _detect_os_language() -> str:
    """OT interfeys tilini aniqlaydi (uz/ru/en). Topilmasa — en."""
    code = ""
    try:
        if platform.system() == "Windows":
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lang_id & 0x3FF
            code = {0x19: "ru", 0x43: "uz", 0x09: "en"}.get(primary, "")
        if not code:
            loc = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
            loc = loc.lower()
            if loc.startswith(("ru", "russian")):
                code = "ru"
            elif loc.startswith(("uz", "uzbek")):
                code = "uz"
            elif loc.startswith(("en", "english")):
                code = "en"
    except Exception:
        code = ""
    return code if code in SUPPORTED else "en"


def initial_language(saved: str | None) -> str:
    """Saqlangan tanlov bo'lsa uni, aks holda OT tilini qaytaradi."""
    if saved in SUPPORTED:
        return saved
    return _detect_os_language()


def set_language(code: str) -> None:
    global _current
    _current = code if code in SUPPORTED else "uz"


def current_language() -> str:
    return _current


def t(text: str, *args: object) -> str:
    """O'zbekcha manba satrni joriy tilga o'giradi (topilmasa o'zini)."""
    if _current == "uz":
        result = text
    else:
        result = _STRINGS.get(text, {}).get(_current, text)
    if args:
        try:
            return result.format(*args)
        except Exception:
            return result
    return result
