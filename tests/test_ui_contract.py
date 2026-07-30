"""Interfeys sinfining «shartnomasi» — dizayn o'zgarishlari buzmasligi uchun.

NEGA KERAK (2026-07-30 jonli nosozlik): dizaynni almashtirganda
`_build_ui` metodi butunlay qayta yozildi va kesish chizig'i undan
KEYINGI metodning `@staticmethod` dekoratorini ham olib ketdi. Natijada
`self._tray_pixmap()` chaqirilganda `self` `size` argumenti o'rniga
tushib, `QPixmap(self, self)` bo'ldi va dastur ochilishida yiqildi:

    Failed to execute script 'product_app' due to unhandled exception:
    'PySide6.QtGui.QPixmap.__init__' called with wrong argument types

macOS'dagi offscreen sinov buni TUTMADI, chunki u yerda tizim tray'i
yo'q — `_build_tray` `_tray_pixmap` ga yetib bormasdan qaytadi. Shuning
uchun tekshiruv Qt oynasidan mustaqil bo'lishi kerak.
"""

from __future__ import annotations

import inspect
import unittest

import product_app


class StaticMethodContractTests(unittest.TestCase):
    """`self` qabul qilmaydigan metod ALBATTA `@staticmethod` bo'lishi shart."""

    def test_methods_without_self_are_static(self) -> None:
        missing: list[str] = []
        for cls in (
            product_app.TranslatorWindow,
            product_app.SettingsDialog,
            product_app.OutputPickerDialog,
        ):
            for name, value in vars(cls).items():
                raw = inspect.getattr_static(cls, name)
                if isinstance(raw, (staticmethod, classmethod, property)):
                    continue
                if not inspect.isfunction(value):
                    continue
                first = next(iter(inspect.signature(value).parameters), "")
                if first not in {"self", "cls"}:
                    missing.append(f"{cls.__name__}.{name} (1-arg: {first!r})")
        self.assertEqual(
            missing,
            [],
            "Bu metodlar `self` olmaydi, demak `@staticmethod` bo'lishi shart — "
            "aks holda chaqirilganda `self` birinchi argument o'rniga tushadi: "
            + ", ".join(missing),
        )

    def test_tray_icon_builder_is_static(self) -> None:
        """Tray ikonasi Qt oynasisiz ham yasalishi kerak."""
        raw = inspect.getattr_static(product_app.TranslatorWindow, "_tray_pixmap")
        self.assertIsInstance(
            raw,
            staticmethod,
            "_tray_pixmap `@staticmethod` bo'lishi SHART: u `self` olmaydi va "
            "`self._tray_pixmap()` shaklida chaqiriladi.",
        )


if __name__ == "__main__":
    unittest.main()
