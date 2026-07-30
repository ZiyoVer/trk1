"""Cross-platform desktop shell and first-run setup for Live Translator."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import plistlib
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request
import uuid
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path


if "--engine" in sys.argv:
    # Windows'da QProcess bergan quvur lokal kod sahifasida ochiladi
    # (rus tizimida cp1251) va birinchi "✓" belgisi UnicodeEncodeError
    # bilan dvigatelni yiqitadi. UTF-8 ga o'tkazamiz, iloji bo'lmasa
    # xatoli belgilarni almashtiramiz.
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None:
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    # A windowed PyInstaller child can start without valid stdout/stderr file
    # descriptors. The GUI consumes this mirrored log, so attach it first and
    # never depend on a fragile QProcess pipe for engine status.
    engine_log_path = os.getenv("LIVE_TRANSLATOR_ENGINE_LOG", "").strip()
    if engine_log_path:
        log_path = Path(engine_log_path).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8", buffering=1)

        class _Tee:
            def __init__(self, stream, mirror) -> None:  # noqa: ANN001
                self.stream = stream
                self.mirror = mirror

            def write(self, data: str) -> int:
                self.mirror.write(data)
                try:
                    return self.stream.write(data)
                except (UnicodeEncodeError, ValueError, OSError):
                    # Quvur yopilgan yoki belgini kodlay olmadi — log fayli
                    # asosiy manba, dvigatel shu sababdan to'xtamasin.
                    return len(data)

            def flush(self) -> None:
                self.mirror.flush()
                try:
                    self.stream.flush()
                except (ValueError, OSError):
                    pass

        sys.stdout = log_file if sys.stdout is None else _Tee(sys.stdout, log_file)
        sys.stderr = log_file if sys.stderr is None else _Tee(sys.stderr, log_file)
    else:
        fallback = open(os.devnull, "w", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = fallback
        if sys.stderr is None:
            sys.stderr = fallback
    sys.argv.remove("--engine")
    from translator import main

    raise SystemExit(main())


import keyring
import sounddevice as sd
from dotenv import dotenv_values
from PySide6.QtCore import (
    QObject,
    QPoint,
    QRectF,
    QProcess,
    QProcessEnvironment,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QPainter,
    QActionGroup,
    QColor,
    QCursor,
    QDesktopServices,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QSizePolicy,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from audio import (
    available_devices,
    auto_input_device,
    auto_output_device,
    is_alias_output,
    preferred_physical_output,
)
import ui_i18n
from ui_i18n import t
from audio_routing import (
    AudioEndpoint,
    DuplexRoutes,
    is_forbidden_route,
    is_virtual_device,
    validate_duplex_routes,
    virtual_device_family,
)
from language_config import (
    APP_MODE_BY_CODE,
    APP_MODES,
    PRODUCT_MODES,
    SOURCE_LANGUAGES,
    TARGET_CODES,
    TARGET_LANGUAGES,
    LanguagePair,
    change_source,
    change_target,
    duplex_label,
    language_caption,
    normalize_pair,
    pair_label,
    swap_pair,
)
from licensing import (
    LicenseClient,
    LicenseError,
    ensure_ca_bundle_env,
    secure_ssl_context,
    validate_control_url,
)
from system_audio import (
    InputDevice,
    OutputDevice,
    default_input as system_default_input,
    default_output as system_default_output,
    route_input_to,
    route_output_to,
    set_default_input,
    set_default_output,
)


APP_NAME = "Live Translator"
# Windows'ning RASMIY ikon shrifti (Segoe Fluent Icons — Win11, MDL2 — Win10).
# Tizimda tayyor turadi, hech narsa o'rnatilmaydi. macOS'da (faqat dev) oddiy
# belgi ko'rsatiladi.
ICON_FONT = "font-family: 'Segoe Fluent Icons', 'Segoe MDL2 Assets';"


def fluent_glyph(code: int, fallback: str) -> str:
    """Windows'da rasmiy Fluent belgisi, boshqa tizimda zaxira matn."""
    return chr(code) if platform.system() == "Windows" else fallback


HEADER_BUTTON_STYLE = (
    "QPushButton { background: #ffffff; color: #3a3a40; font-size: 13px; padding: 0; " + ICON_FONT + " "
    "border: 1px solid #e4e7ec; border-radius: 10px; } "
    "QPushButton:hover { background: #f2f4f7; } "
    "QPushButton:pressed { background: #e8eaee; }"
)
CLOSE_BUTTON_STYLE = (
    "QPushButton { background: #ffffff; color: #3a3a40; font-size: 12px; padding: 0; " + ICON_FONT + " "
    "border: 1px solid #e4e7ec; border-radius: 10px; } "
    "QPushButton:hover { background: #fdeceb; color: #c42b1c; border-color: #f5c9c5; } "
    "QPushButton:pressed { background: #f8dcd9; }"
)
CHECKBOX_STYLE = (
    "QCheckBox { color: #9fb0c6; font-size: 11px; font-weight: 600; spacing: 7px; } "
    "QCheckBox::indicator { width: 13px; height: 13px; border-radius: 4px; "
    "border: 1px solid #33456080; background: #131e30; } "
    "QCheckBox::indicator:checked { background: #15845a; border-color: #15845a; }"
)
APP_VERSION = "0.9.92"


def _read_channel() -> str:
    """Qaysi oqimdan yangilanish olinadi: "ops" yoki "" (oddiy Windows).

    Nima uchun: monitorli (sinov) kompyuter yangi versiyalarni BIRINCHI
    bo'lib oladi, boshliq va hamkasblarning kompyuterlari esa faqat
    barqaror versiyada qoladi — ular ARALASHMASLIGI kerak. Oqim nomi
    ilova papkasidagi `channel.txt` faylida turadi; uni "Windows OPS"
    o'rnatuvchisi yozadi, oddiy o'rnatuvchi esa o'chiradi.
    """
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    try:
        value = (base / "channel.txt").read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        return ""
    return value if value in {"ops"} else ""


APP_CHANNEL = _read_channel()
APP_EDITION = "Windows OPS" if APP_CHANNEL == "ops" else "Windows"

# Yordam serveri (Railway): loglarni yuborish va yangilanishni tekshirish.
# Yuklash tokeni ilova ichida bo'lishi SHART (foydalanuvchi bilmaydi) —
# u faqat log YOZISH huquqini beradi, o'qish alohida admin token bilan.
SUPPORT_URL = os.getenv("LT_SUPPORT_URL", "https://lt-support-production.up.railway.app")
SUPPORT_UPLOAD_TOKEN = os.getenv("LT_SUPPORT_TOKEN", "zuioC-x9vEAHXFr4bheYtVCfPDMqNqrM")
KEYRING_SERVICE = "local.live-translator"
KEYRING_ACCOUNT = "edcom-api-key"
KEYRING_LICENSE_ACCOUNT = "license-key"
KEYRING_CONTROL_URL_ACCOUNT = "control-url"
KEYRING_DEVICE_ACCOUNT = "device-id"
PROJECT_DIR = Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Bundlangan yordamchi fayl yo'li (dev va PyInstaller frozen uchun)."""
    base = getattr(sys, "_MEIPASS", None)
    candidates = []
    if base:
        candidates.append(Path(base) / name)
    candidates += [
        PROJECT_DIR / name,
        PROJECT_DIR / "packaging" / "windows" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else PROJECT_DIR / name
BLACKHOLE_URL = "https://existential.audio/downloads/BlackHole2ch-0.7.1.pkg"
BLACKHOLE_SHA256 = "57b540f27a3e29c37e310e01bee0fdfab76733087e47f997ef9dccf851400dcf"
BLACKHOLE_16CH_URL = "https://existential.audio/downloads/BlackHole16ch-0.7.1.pkg"
BLACKHOLE_16CH_SHA256 = "57254e2f76cd40db7f3f715238b1a2cb2bd08819d38abf4087f2944f71a3641a"
VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VBCABLE_SHA256 = "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb"
# Duplex uchun IKKINCHI mustaqil kabel — VB-Audio Hi-Fi Cable (end-user
# uchun BEPUL, A+B donationware'ga muqobil). Real Windows'da tekshirilgan.
HIFI_CABLE_URL = "https://download.vb-audio.com/Download_CABLE/HiFiCableAsioBridgeSetup_v1007.zip"
HIFI_CABLE_SHA256 = "3ecf204bfd8579d36bb918f9856eb1eaddd49c75146f5e8a8f59dcb8375ae89a"
BLACKHOLE_DRIVER_PATH = Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver")
BLACKHOLE_16CH_DRIVER_PATH = Path("/Library/Audio/Plug-Ins/HAL/BlackHole16ch.driver")


def is_engine_connected_line(line: str) -> bool:
    return "ulandi." in line.casefold()


def is_expected_engine_exit(exit_code: int, stop_requested: bool) -> bool:
    """A user-requested process exit is a normal Stop, not a crash."""

    return stop_requested or exit_code == 0


class Waveform(QWidget):
    """Jonli ovoz tasmasi — nutq kelganda harakatlanadi, jimlikda tinchlanadi.

    QOTISH XAVFI YO'Q (0.9.78 saboqidan): u yerda muammo sekundiga 5 marta
    `setStyleSheet` + tray tooltip yangilash edi (uslub qayta tahlili +
    Windows shell chaqiruvi). Bu yerda esa:
      • taymer FAQAT tarjima ishlayotganda yuradi (80 ms);
      • har tik faqat `update()` — kichkina 30 px maydonni qayta chizish,
        hech qanday uslub/layout ishi yo'q;
      • ovoz darajasi tashqaridan `pulse()` bilan keladi (transkript satri
        kelganda) va o'z-o'zidan so'nadi — davriy so'rov yo'q.
    """

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._phase = 0
        self._level = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def set_running(self, running: bool) -> None:
        if running:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._level = 0.0
            self.update()

    def pulse(self) -> None:
        """Nutq keldi — to'lqin jonlanadi (keyin o'zi so'nadi)."""
        self._level = 1.0

    def _tick(self) -> None:
        self._phase += 1
        self._level = max(0.0, self._level * 0.94 - 0.004)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1a73e8"))
        width = self.width()
        height = self.height()
        step = 6.0
        count = max(8, int(width / step))
        middle = height / 2
        for index in range(count):
            # Deterministik "tasodifiy" balandlik: ikki sinus qorishmasi,
            # faza siljishi harakat beradi, `_level` amplituda beradi.
            ripple = abs(
                math.sin(0.9 * index + self._phase * 0.55)
                * math.sin(1.7 * index - self._phase * 0.33)
            )
            bar = max(2.0, height * (0.08 + 0.84 * self._level * ripple))
            x = index * step
            painter.drawRoundedRect(
                QRectF(x, middle - bar / 2, 2.6, bar), 1.2, 1.2
            )

class ActionTile(QFrame):
    """Keng tugma-plitka: yoqilganda rangli, o'chirilganda oq."""

    clicked = Signal()

    def __init__(self, glyph: str, text: str, accent: str, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._accent = accent
        self._active = False
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(14)
        self.icon = QLabel(glyph)
        self.icon.setStyleSheet(f"font-size: 14px; background: transparent; {ICON_FONT}")
        self.label = QLabel(text)
        self.label.setStyleSheet(
            "font-size: 13.5px; font-weight: 600; background: transparent;"
        )
        row.addStretch()
        row.addWidget(self.icon)
        row.addWidget(self.label)
        row.addStretch()
        self._apply()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._apply()

    def is_active(self) -> bool:
        return self._active

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def _apply(self) -> None:
        if self._active:
            self.setStyleSheet(
                f"ActionTile {{ background: {self._accent}; border: 1px solid {self._accent};"
                " border-radius: 13px; }"
            )
            colour = "#ffffff"
        else:
            self.setStyleSheet(
                "ActionTile { background: #ffffff; border: 1px solid #e4e7ec;"
                " border-radius: 13px; }"
            )
            colour = "#1b1b1f"
        self.icon.setStyleSheet(
            f"font-size: 14px; color: {colour}; background: transparent; {ICON_FONT}"
        )
        self.label.setStyleSheet(
            f"font-size: 13.5px; font-weight: 600; color: {colour}; background: transparent;"
        )

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class OutputPickerDialog(QDialog):
    """«Tarjima ovozini qayerdan eshitasiz?» — birinchi ishga tushirishda.

    NEGA KERAK: ilova tarjimani Windows'ning ASOSIY qurilmasiga chiqaradi,
    lekin odam ko'pincha boshqasidan eshitadi (masalan monitor karnayi
    "PC Monitor (Аудио Intel для дисплеев)", tizim default'i esa Realtek).
    Natijada tarjima matni ko'rinadi, ovozi eshitilmaydi — bir necha
    kompyuterda aynan shu takrorlandi. Dastur odam qayerdan eshitayotganini
    BILA OLMAYDI, shuning uchun BIR MARTA so'raydi va javobni saqlaydi.
    """

    def __init__(self, devices: list, current: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle(t("Ovoz qurilmasi"))
        self.setMinimumWidth(460)
        self.setObjectName("outputPicker")
        # Asosiy oyna bilan bir xil yorug' mavzu.
        self.setStyleSheet(
            """
            QDialog#outputPicker { background: #f7f8fa; }
            QLabel { color: #1b1b1f; font-size: 13px; }
            QComboBox { background: #ffffff; color: #1b1b1f; border: 1px solid #e4e7ec;
                        border-radius: 10px; padding: 9px 12px; font-size: 13.5px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #1b1b1f;
                                         selection-background-color: #e9f0fa;
                                         selection-color: #1b1b1f; border: 1px solid #e4e7ec; }
            QPushButton { background: #ffffff; color: #3a3a40; border: 1px solid #e4e7ec;
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }
            QPushButton:hover { background: #f2f4f7; }
            """
        )
        self.chosen = ""
        layout = QVBoxLayout(self)
        title = QLabel(t("Tarjima ovozini qaysi qurilmadan eshitasiz?"))
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #101114;")
        layout.addWidget(title)
        hint = QLabel(
            t(
                "Ro‘yxatdan tanlang va «▶ Sinov» bosing. Ovoz eshitilsa — "
                "qurilma to‘g‘ri. Bu bir martalik sozlama."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b5b66; font-size: 12.5px;")
        layout.addWidget(hint)
        self.combo = QComboBox()
        for device in devices:
            self.combo.addItem(device.name, device.index)
            self.combo.setItemData(
                self.combo.count() - 1, device.name, Qt.ItemDataRole.UserRole + 1
            )
        if current:
            position = self.combo.findText(current)
            if position >= 0:
                self.combo.setCurrentIndex(position)
        layout.addWidget(self.combo)
        row = QHBoxLayout()
        self.test_button = QPushButton("▶ " + t("Sinov"))
        self.test_button.clicked.connect(self._test)
        row.addWidget(self.test_button)
        self.result = QLabel("")
        self.result.setStyleSheet("color: #1a73e8; font-size: 12.5px;")
        row.addWidget(self.result, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test(self) -> None:
        index = self.combo.currentData()
        if index is None:
            return
        try:
            import numpy as np
            import sounddevice as sd

            rate = 48_000
            t_axis = np.arange(int(rate * 0.6), dtype=np.float32) / rate
            tone = 0.25 * np.sin(2 * np.pi * 523.25 * t_axis)
            half = len(t_axis) // 2
            tone[half:] = 0.25 * np.sin(2 * np.pi * 659.25 * t_axis[: len(t_axis) - half])
            sd.play(tone, samplerate=rate, device=int(index), blocking=False)
            self.result.setText(t("Ovoz yuborildi — eshitildimi?"))
        except Exception as error:
            self.result.setText(f"{error}")

    def _accept(self) -> None:
        self.chosen = str(self.combo.currentText())
        self.accept()


class DriverSignals(QObject):
    ready = Signal(str)
    failed = Signal(str)


class DeviceScanSignals(QObject):
    """Qurilmalarni sanash NATIJALARI (fon oqimidan GUI'ga)."""

    signature = Signal(tuple)
    drivers = Signal(list)
    routing = Signal(str, str, str)   # (karnay, mikrofon, fizik chiqish)


class SupportSignals(QObject):
    finished = Signal(str)


class UpdateSignals(QObject):
    available = Signal(str, str)
    downloaded = Signal(str)


class LicenseSignals(QObject):
    activated = Signal(str)
    failed = Signal(str)
    heartbeat_ok = Signal()
    heartbeat_failed = Signal(str)


class DirectionSelector(QFrame):
    """Always-visible product modes with a strong selected state."""

    currentIndexChanged = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("directionSelector")
        self._index = -1
        self._items = items
        self._buttons: list[QPushButton] = []
        self.setFixedHeight(60)
        self.setStyleSheet(
            """
            QFrame#directionSelector {
                background: #111c2e; border-radius: 10px;
            }
            QFrame#directionSelector QPushButton {
                background: transparent; color: #8fa0b7; border: 0;
                border-radius: 8px; padding: 6px 11px; text-align: left;
                font-size: 11px; font-weight: 700;
            }
            QFrame#directionSelector QPushButton:hover:!checked {
                background: #1b2940; color: #f1f5f9;
            }
            QFrame#directionSelector QPushButton:checked {
                background: #2f6fed; color: white;
            }
            QFrame#directionSelector QPushButton:pressed {
                background: #2458bd; color: white;
            }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        for index, (task, languages) in enumerate(items):
            button = QPushButton(f"{task}\n{languages}")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(
                lambda _checked=False, selected=index: self.setCurrentIndex(selected)
            )
            layout.addWidget(button, 1)
            self._buttons.append(button)
        self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._index

    def currentText(self) -> str:
        return self._items[self._index][1]

    def setItemLanguages(self, index: int, languages: str) -> None:
        if not 0 <= index < len(self._buttons):
            return
        task, _old_languages = self._items[index]
        self._items[index] = (task, languages)
        self._buttons[index].setText(f"{task}\n{languages}")

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._buttons):
            return
        changed = index != self._index
        self._index = index
        self._buttons[index].setChecked(True)
        if changed:
            self.currentIndexChanged.emit(index)


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        api_key: str = "",
        control_url: str = "",
        license_key: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("apiKeyDialog")
        self.setWindowTitle("Live Translator sozlamalari")
        # Asosiy oyna bilan BIR XIL kenglik (385) — foydalanuvchi talabi:
        # "settings page ham kichik bo'lsin asosiy page kabi".
        self.setFixedSize(385, 356)
        # Asosiy oyna bilan BIR XIL yorug' mavzu (0.9.82 maketi). Ilgari bu
        # oyna qorong'i qolib, ikkisi bir-biriga mos kelmasdi.
        self.setStyleSheet(
            """
            QDialog#apiKeyDialog { background: #f7f8fa; }
            QLabel { color: #1b1b1f; font-size: 13.5px; }
            QLineEdit { background: #ffffff; color: #1b1b1f; border: 1px solid #e4e7ec;
                        border-radius: 9px; padding: 8px 11px; font-size: 13px;
                        selection-background-color: #cfe0fb; selection-color: #1b1b1f; }
            QLineEdit:focus { border-color: #1a73e8; }
            QPushButton { background: #ffffff; color: #3a3a40; border: 1px solid #e4e7ec;
                          border-radius: 9px; padding: 6px 14px; font-weight: 600; }
            QPushButton:hover { background: #f2f4f7; }
            """
        )
        layout = QVBoxLayout(self)
        title = QLabel("Ulanish va litsenziya")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #101114;")
        info = QLabel(
            "Maxfiy qiymatlar faqat tizim Keychain/Credential Manager ichida saqlanadi."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #5b5b66; font-size: 12.5px;")
        api_label = QLabel("Gemini API key")
        api_label.setStyleSheet("color: #5b5b66; font-size: 12px; font-weight: 600;")
        self.api_input = QLineEdit(api_key)
        self.api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_input.setPlaceholderText("Google AI Studio API key")
        link = QLabel(
            "Bu kalit Gemini 3.5 Live Translate’ga ulanish uchun ishlatiladi."
        )
        link.setStyleSheet("color: #1a73e8; font-size: 12.5px;")
        server_label = QLabel("Boshqaruv serveri")
        server_label.setStyleSheet("color: #5b5b66; font-size: 12px; font-weight: 600;")
        self.control_input = QLineEdit(control_url)
        self.control_input.setPlaceholderText("https://control.example.com — bo‘sh bo‘lsa developer mode")
        license_label = QLabel("Litsenziya kaliti")
        license_label.setStyleSheet("color: #5b5b66; font-size: 12px; font-weight: 600;")
        self.license_input = QLineEdit(license_key)
        self.license_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.license_input.setPlaceholderText("LT-XXXXXX-XXXXXX-XXXXXX-XXXXXX")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save_button.setText("Saqlash")
        save_button.setStyleSheet(
            "QPushButton { background: #1a73e8; color: white; border: 0; "
            "border-radius: 9px; padding: 6px 18px; font-weight: 600; } "
            "QPushButton:hover { background: #1668d4; }"
        )
        cancel_button.setText("Bekor qilish")
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addWidget(api_label)
        layout.addWidget(self.api_input)
        layout.addWidget(link)
        layout.addWidget(server_label)
        layout.addWidget(self.control_input)
        layout.addWidget(license_label)
        layout.addWidget(self.license_input)
        layout.addWidget(buttons)

    @property
    def api_key(self) -> str:
        return self.api_input.text().strip()

    @property
    def control_url(self) -> str:
        return self.control_input.text().strip()

    @property
    def license_key(self) -> str:
        return self.license_input.text().strip()


class TranslatorWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(385, 392)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.process: QProcess | None = None
        self.stop_requested = False
        self.quit_requested = False
        self.minimize_hint_shown = False
        self.connected = False
        self.connected_channels: set[str] = set()
        self.output_buffer = ""
        self.last_engine_error = ""
        self.process_error = ""
        self.engine_log_path = self._engine_log_path()
        self.engine_log_position = 0
        self.previous_system_output: OutputDevice | None = None
        self.previous_system_input: InputDevice | None = None
        # Windows: Start'da saqlangan tizim default qurilmalari.
        self.win_prev_render = ""
        self.win_prev_capture = ""
        self.driver_install_prompted = False
        self.driver_variant = "2ch"
        self.source_caption = ""
        self.target_caption = ""
        self.channel_captions = {
            "INCOMING": {"source": "", "target": ""},
            "OUTGOING": {"source": "", "target": ""},
        }
        self.settings = QSettings("Charon", APP_NAME)
        # Tarjima ovozi qaysi qurilmadan eshitilishi — foydalanuvchi tanlovi
        # (saqlanadi). Bo'sh bo'lsa avtomatik tanlanadi.
        self.preferred_output_name = str(
            self.settings.value("preferred_output", "") or ""
        )
        # Ilgari saqlangan SOXTA tanlov (yo'naltirgich) bekor qilinadi —
        # aks holda yangilanishdan keyin ham o'sha nosozlik qolaverardi.
        if self.preferred_output_name and is_alias_output(self.preferred_output_name):
            print(
                f"[ROUTING] soxta chiqish tanlovi bekor qilindi: "
                f"{self.preferred_output_name!r}",
                flush=True,
            )
            self.preferred_output_name = ""
            self.settings.remove("preferred_output")
        # Interfeys tili: saqlangan tanlov yoki OT tili (uz/ru/en).
        ui_i18n.set_language(
            ui_i18n.initial_language(
                str(self.settings.value("ui/language", "")) or None
            )
        )
        # "Gapirish"da tarjima virtual kabelga ketadi; nazorat ovozi uni
        # naushnikda ham eshittiradi (default: yoqiq).
        self.monitor_enabled = (
            str(self.settings.value("audio/monitor_outgoing", "false")).lower() == "true"
        )
        # «MEETING O'ZBEKCHA» — kiruvchi tarjima UMUMAN ishga tushmaydi.
        #
        # Nima uchun kerak (2026-07-28, Zoom ko'rsatuvi): kiruvchi yo'nalish
        # AUTO → O'ZBEK. Meetingdagi odamlar o'zbekcha gapirsa, tarjima
        # qiladigan narsa qolmaydi — model jim turadi. Ayni paytda dastur
        # meeting ovozini karnayga emas, virtual kabelga burib yuborgan
        # bo'ladi, ya'ni asl ovoz ham eshitilmaydi. Natija: xona JIMJIT
        # (logda o'sha payt bironta [INCOMING] satri yo'q).
        #
        # Bu rejimda tizim chiqishiga UMUMAN tegilmaydi: meeting ovozi
        # karnaydan jonli, o'z ovozida eshitiladi; faqat SIZNING gapingiz
        # tarjima bo'lib meetingga boradi.
        self.meeting_uz = (
            str(self.settings.value("translation/meeting_uz", "false")).lower() == "true"
        )
        # «Sifatli tarjima»: gap tugashini kutib, TO'LIQ gapni tarjima qiladi
        # (sinxron model bo'lak-bo'lak tarjima qilib ma'noni buzardi).
        self.quality_mode = (
            str(self.settings.value("translation/quality", "false")).lower() == "true"
        )
        self.mode_pairs = {
            mode.code: normalize_pair(
                mode.code,
                str(
                    self.settings.value(
                        f"translation/{mode.code}/source", mode.default_pair.source
                    )
                ),
                str(
                    self.settings.value(
                        f"translation/{mode.code}/target", mode.default_pair.target
                    )
                ),
            )
            for mode in PRODUCT_MODES
        }
        # Ilova endi faqat ikki tomonlama ishlaydi.
        self.initial_mode = "duplex"
        self.language_change_in_progress = False
        self.api_key = self._load_api_key()
        self.control_url = self._load_keyring(KEYRING_CONTROL_URL_ACCOUNT) or self._default_control_url()
        self.license_key = self._load_keyring(KEYRING_LICENSE_ACCOUNT)
        self.device_id = self._load_or_create_device_id()
        self.license_client: LicenseClient | None = None
        self.license_check_in_progress = False
        self.heartbeat_in_progress = False
        self.heartbeat_failures = 0
        self.audio_devices_initialized = False
        # Qurilma skanerlari FON oqimida ishlaydi (pastdagi izohga qarang).
        self._sd_lock = threading.Lock()
        self._routing_lock = threading.Lock()
        self._scan_busy = False
        self._driver_scan_busy = False
        self.device_scan_signals = DeviceScanSignals()
        self.device_scan_signals.signature.connect(self._device_signature_ready)
        self.device_scan_signals.drivers.connect(self._apply_driver_state)
        self.device_scan_signals.routing.connect(self._routing_applied)
        self.support_signals = SupportSignals()
        self.support_signals.finished.connect(self._support_finished)
        self.update_signals = UpdateSignals()
        self.update_signals.available.connect(self._update_available)
        self.update_signals.downloaded.connect(self._update_downloaded)
        self.update_url = ""
        self.driver_signals = DriverSignals()
        self.driver_signals.ready.connect(self._driver_installer_ready)
        self.driver_signals.failed.connect(self._driver_installer_failed)
        self.license_signals = LicenseSignals()
        self.license_signals.activated.connect(self._license_activated)
        self.license_signals.failed.connect(self._license_failed)
        self.license_signals.heartbeat_ok.connect(self._heartbeat_ok)
        self.license_signals.heartbeat_failed.connect(self._heartbeat_failed)
        self._build_ui()
        self._refresh_driver_state()
        QTimer.singleShot(250, self._first_run)
        QTimer.singleShot(4000, self._check_for_update)
        # Ovoz qurilmasini BIR MARTA so'raymiz (saqlanmagan bo'lsa).
        QTimer.singleShot(1500, self.ask_output_device)
        self.driver_timer = QTimer(self)
        self.driver_timer.timeout.connect(self._refresh_driver_state)
        self.driver_timer.start(4000)
        self.connection_timer = QTimer(self)
        self.connection_timer.setSingleShot(True)
        self.connection_timer.timeout.connect(self._connection_timed_out)
        self.engine_log_timer = QTimer(self)
        self.engine_log_timer.setInterval(100)
        self.engine_log_timer.timeout.connect(self._read_engine_log)
        # Windows: sessiya davomida naushnik ulanishini kuzatadi.
        self.device_signature: tuple[str, ...] = ()
        self.device_state_path = log_directory() / "devices.json"
        self.device_change_timer = QTimer(self)
        self.device_change_timer.setInterval(3000)
        self.device_change_timer.timeout.connect(self._check_device_changes)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(60_000)
        self.heartbeat_timer.timeout.connect(self._send_heartbeat)

    def _build_ui(self) -> None:
        """2026-07-29 maketi. Mantiqqa TEGILMAGAN: barcha widget nomlari va
        signallari eskisidek. Maketda joyi yo'q widgetlar `_hidden_hints`
        (ko'rinmas ota) ichida yashaydi — kod ularga matn yozaveradi,
        ekranda chiqmaydi."""
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(
            """
            QFrame#card { background: #f7f8fa; border-radius: 16px; }
            QLabel { color: #1b1b1f; background: transparent; }
            QComboBox { background: #ffffff; color: #1b1b1f; border: 1px solid #e4e7ec;
                        border-radius: 9px; padding: 6px 12px; min-height: 20px;
                        font-size: 13.5px; font-weight: 500; }
            QComboBox:hover { border-color: #c9ced6; }
            QComboBox::drop-down { border: 0; width: 20px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #1b1b1f;
                                         selection-background-color: #e9f0fa;
                                         selection-color: #1b1b1f; border: 1px solid #e4e7ec; }
            QPushButton { border: 0; border-radius: 9px; padding: 8px 13px;
                          color: white; font-weight: 600; }
            """
        )
        root.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._hidden_hints = QWidget(self)
        self._hidden_hints.setVisible(False)
        hidden = QVBoxLayout(self._hidden_hints)

        # ----------------- SARLAVHA -----------------
        header = QHBoxLayout()
        header.setSpacing(8)
        logo = QLabel(fluent_glyph(0xE767, "‖"))
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            "background: #1a73e8; color: white; border-radius: 15px; "
            f"font-size: 13px; font-weight: 800; {ICON_FONT}"
        )
        title = QLabel("Live Translator")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #101114;")
        self.status = QLabel("●  Tayyor")
        self.status.setStyleSheet("color: #0f8b96; font-size: 10.5px; font-weight: 500;")
        settings = QPushButton(fluent_glyph(0xE713, "⚙"))
        settings.setAccessibleName("Sozlamalar")
        settings.setToolTip("Sozlamalar")
        settings.setFixedSize(28, 26)
        settings.setStyleSheet(HEADER_BUTTON_STYLE)
        settings.clicked.connect(self.edit_settings)
        close = QPushButton(fluent_glyph(0xE8BB, "✕"))
        close.setAccessibleName("Yopish")
        close.setToolTip("Yopish")
        close.setFixedSize(28, 26)
        close.setStyleSheet(CLOSE_BUTTON_STYLE)
        close.clicked.connect(self.close)
        header.addWidget(logo)
        header.addWidget(title)
        header.addSpacing(6)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(settings)
        header.addWidget(close)
        layout.addLayout(header)

        # ----------------- DRAYVER QATORI -----------------
        self.driver_row = QFrame()
        driver_layout = QHBoxLayout(self.driver_row)
        driver_layout.setContentsMargins(14, 8, 10, 8)
        self.driver_label = QLabel("")
        self.driver_label.setWordWrap(True)
        self.driver_label.setStyleSheet("color: #7a4b00; font-size: 12.5px;")
        self.driver_button = QPushButton("AUDIO DRIVER O‘RNATISH")
        self.driver_button.setStyleSheet("background: #b45309;")
        self.driver_button.clicked.connect(self.install_driver)
        driver_layout.addWidget(self.driver_label, 1)
        driver_layout.addWidget(self.driver_button)
        self.driver_row.setStyleSheet(
            "background: #fff4e5; border: 1px solid #ffd8a8; border-radius: 11px;"
        )
        self.driver_row.setVisible(False)
        layout.addWidget(self.driver_row)

        # ----------------- IKKI KATTA TUGMA -----------------
        self.start_button = QPushButton("▶  Tarjimani boshlash", self._hidden_hints)
        self.start_button.clicked.connect(self.start_translator)
        self.stop_button = QPushButton("■  To‘xtatish", self._hidden_hints)
        self.stop_button.clicked.connect(self.stop_translator)
        self.quality_check = QCheckBox("Sifatli tarjima", self._hidden_hints)
        self.quality_check.setChecked(self.quality_mode)
        self.quality_check.toggled.connect(self._toggle_quality)

        self.tile_run = ActionTile(fluent_glyph(0xE768, "▶"), "Boshlash", "#0f8b96")
        self.tile_run.clicked.connect(self._toggle_translation)
        self.tile_quality = ActionTile(fluent_glyph(0xE734, "✦"), "Sifatli tarjima", "#1a73e8")
        self.tile_quality.set_active(self.quality_mode)
        self.tile_quality.clicked.connect(
            lambda: self.quality_check.setChecked(not self.quality_check.isChecked())
        )
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        tiles.addWidget(self.tile_run, 1)
        tiles.addWidget(self.tile_quality, 1)
        layout.addLayout(tiles)

        # ----------------- IKKI PANEL -----------------
        self.your_language_select = QComboBox()
        self.meeting_language_select = QComboBox()
        for language in TARGET_LANGUAGES:
            self.your_language_select.addItem(language.name, language.code)
            self.meeting_language_select.addItem(language.name, language.code)
        for combo in (self.your_language_select, self.meeting_language_select):
            combo.setFixedWidth(116)
            combo.setCursor(Qt.CursorShape.PointingHandCursor)

        self.caption_panel = QFrame()
        self.caption_panel.setObjectName("captionPanel")
        self.caption_panel.setStyleSheet(
            "QFrame#captionPanel { background: #ffffff; border: 1px solid #e4e7ec;"
            " border-radius: 13px; }"
        )
        panes = QHBoxLayout(self.caption_panel)
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(0)

        heard = QWidget()
        heard_layout = QVBoxLayout(heard)
        heard_layout.setContentsMargins(14, 12, 12, 14)
        heard_layout.setSpacing(10)
        heard_head = QHBoxLayout()
        # DIQQAT: ikon yorlig'i AJRATILDI. Ilgari bu yerda `source_language`
        # turardi, lekin dvigatel matni («Meeting · UZ») aynan shu widgetga
        # yoziladi — natijada tarjima paytida ikon o'rnida «Meeting Meeting»
        # yozuvi chiqib qolardi (2026-07-30 jonli nosozlik).
        self._mic_icon = QLabel(fluent_glyph(0xE720, "\U0001F3A4"))
        self._mic_icon.setStyleSheet(f"font-size: 17px; color: #1a73e8; {ICON_FONT}")
        heard_head.addWidget(self._mic_icon)
        heard_head.addStretch()
        heard_head.addWidget(self.your_language_select)
        self.source_text = QLabel("Gap kutilmoqda…")
        self.source_text.setWordWrap(True)
        self.source_text.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.source_text.setStyleSheet("font-size: 15px; color: #1b1b1f;")
        # Matn uzayganda panel chegaralarini SURMASIN — balandlikni layout
        # emas, panel belgilaydi (matn sig'maganicha qisqaradi).
        self.source_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        heard_layout.addLayout(heard_head)
        heard_layout.addWidget(self.source_text, 1)

        translated = QWidget()
        translated.setObjectName("translatedPane")
        translated.setStyleSheet(
            "QWidget#translatedPane { background: #e9f0fa;"
            " border-top-right-radius: 13px; border-bottom-right-radius: 13px; }"
        )
        translated_layout = QVBoxLayout(translated)
        translated_layout.setContentsMargins(14, 12, 12, 14)
        translated_layout.setSpacing(10)
        translated_head = QHBoxLayout()
        self._translate_icon = QLabel(fluent_glyph(0xE8C1, "文"))
        self._translate_icon.setStyleSheet(
            f"font-size: 17px; color: #5b3fd4; font-weight: 700; {ICON_FONT}"
        )
        translated_head.addWidget(self._translate_icon)
        translated_head.addStretch()
        translated_head.addWidget(self.meeting_language_select)
        self.target_text = QLabel("Tarjima shu yerda chiqadi…")
        self.target_text.setWordWrap(True)
        self.target_text.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.target_text.setStyleSheet("font-size: 15px; color: #1b1b1f;")
        self.target_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        translated_layout.addLayout(translated_head)
        translated_layout.addWidget(self.target_text, 1)

        panes.addWidget(heard, 1)
        panes.addWidget(translated, 1)
        layout.addWidget(self.caption_panel, 1)

        # ----------------- PASTKI TASMA -----------------
        self.duplex_outgoing_caption_panel = QFrame()
        self.duplex_outgoing_caption_panel.setObjectName("duplexCaption")
        self.duplex_outgoing_caption_panel.setStyleSheet(
            "QFrame#duplexCaption { background: #ffffff; border: 1px solid #e4e7ec;"
            " border-radius: 13px; }"
        )
        strip = QHBoxLayout(self.duplex_outgoing_caption_panel)
        strip.setContentsMargins(12, 6, 12, 6)
        # «Meeting tarjimasi» yozuvi va «Siz: …» matni OLIB TASHLANDI
        # (foydalanuvchi talabi) — matn uzayganda to'lqinni surib, chegaralar
        # sakrab turardi. Endi tasmada faqat JONLI to'lqin. Yozuv widgetlari
        # ko'rinmas idishda qoladi (kod ularga matn yozaveradi).
        self.duplex_outgoing_caption_title = QLabel("", self._hidden_hints)
        self.duplex_outgoing_original_text = QLabel("", self._hidden_hints)
        self.wave = Waveform()
        strip.addWidget(self.wave, 1)
        layout.addWidget(self.duplex_outgoing_caption_panel)

        # ----------------- KO'RINMAS WIDGETLAR -----------------
        # Dvigatel matn yozadigan yorliqlar (ekranda ko'rinmaydi).
        self.source_language = QLabel("", self._hidden_hints)
        self.target_language = QLabel("", self._hidden_hints)
        self.route_hint = QLabel("", self._hidden_hints)
        self.meet_mic_hint = QLabel("", self._hidden_hints)
        self.update_hint = QLabel("", self._hidden_hints)
        self.update_button = QPushButton("", self._hidden_hints)
        self.update_button.clicked.connect(self._install_update)
        self.language_label = QLabel("", self._hidden_hints)
        self.signal_label = QLabel("", self._hidden_hints)
        self._legacy_lang_frame = QFrame(self._hidden_hints)
        self.duplex_outgoing_language_panel = QFrame(self._hidden_hints)
        self.duplex_outgoing_audio_panel = QFrame(self._hidden_hints)
        self.duplex_outgoing_target_text = QLabel("", self._hidden_hints)
        self.meeting_uz_check = QCheckBox("Meeting o‘zbekcha", self._hidden_hints)
        self.meeting_uz_check.setChecked(self.meeting_uz)
        self.meeting_uz_check.toggled.connect(self._toggle_meeting_uz)
        self.output_test_button = QPushButton("▶ Sinov", self._hidden_hints)
        self.output_test_button.clicked.connect(self._play_output_test)

        self.input_device = QComboBox(self._hidden_hints)
        self.output_device = QComboBox(self._hidden_hints)
        self.duplex_outgoing_input = QComboBox(self._hidden_hints)
        self.duplex_outgoing_output = QComboBox(self._hidden_hints)
        self.duplex_outgoing_output.setPlaceholderText("BlackHole 16ch kerak")
        self.input_device.currentIndexChanged.connect(self._audio_route_changed)
        self.output_device.currentIndexChanged.connect(self._audio_route_changed)
        self.output_device.activated.connect(self._output_device_picked)
        self.duplex_outgoing_input.currentIndexChanged.connect(self._audio_route_changed)
        self.duplex_outgoing_output.currentIndexChanged.connect(self._audio_route_changed)

        self.source_language_select = QComboBox(self._hidden_hints)
        self.target_language_select = QComboBox(self._hidden_hints)
        self.duplex_outgoing_source = QComboBox(self._hidden_hints)
        self.duplex_outgoing_target = QComboBox(self._hidden_hints)
        for language in SOURCE_LANGUAGES:
            self.source_language_select.addItem(language.name, language.code)
            self.duplex_outgoing_source.addItem(language.name, language.code)
        for language in TARGET_LANGUAGES:
            self.target_language_select.addItem(language.name, language.code)
            self.duplex_outgoing_target.addItem(language.name, language.code)
        self.swap_languages_button = QPushButton("⇄", self._hidden_hints)
        self.swap_languages_button.clicked.connect(self._swap_languages)
        self.direction = DirectionSelector(
            [
                (
                    mode.title,
                    pair_label(self.mode_pairs[mode.code])
                    if mode.code in self.mode_pairs
                    else duplex_label(
                        self.mode_pairs["incoming"], self.mode_pairs["outgoing"]
                    ),
                )
                for mode in APP_MODES
            ],
            self._hidden_hints,
        )
        for widget in (
            self.source_language, self.target_language,
            self.route_hint, self.meet_mic_hint, self.update_hint, self.update_button,
            self.start_button, self.stop_button, self.input_device, self.output_device,
            self.duplex_outgoing_input, self.duplex_outgoing_output,
            self.source_language_select, self.target_language_select,
            self.duplex_outgoing_source, self.duplex_outgoing_target,
            self.swap_languages_button, self.direction, self.quality_check,
            self.meeting_uz_check, self.output_test_button, self.language_label,
            self.signal_label, self._legacy_lang_frame,
            self.duplex_outgoing_language_panel, self.duplex_outgoing_audio_panel,
            self.duplex_outgoing_target_text,
        ):
            hidden.addWidget(widget)

        initial_index = next(
            index
            for index, mode in enumerate(APP_MODES)
            if mode.code == self.initial_mode
        )
        self.direction.setCurrentIndex(initial_index)
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.source_language_select.currentIndexChanged.connect(
            self._source_language_changed
        )
        self.target_language_select.currentIndexChanged.connect(
            self._target_language_changed
        )
        self.duplex_outgoing_source.currentIndexChanged.connect(
            self._duplex_outgoing_source_changed
        )
        self.duplex_outgoing_target.currentIndexChanged.connect(
            self._duplex_outgoing_target_changed
        )
        self._init_simple_languages()
        self.your_language_select.currentIndexChanged.connect(
            self._apply_simple_languages
        )
        self.meeting_language_select.currentIndexChanged.connect(
            self._apply_simple_languages
        )
        self._build_tray()
        self._sync_mode_ui(apply_devices=False)
        self._refresh_audio_devices()
        self._ensure_physical_defaults()
        self._set_controls(running=False)
        self._sync_tiles()

    def _toggle_translation(self) -> None:
        """«Translating» plitkasi: bosilsa boshlaydi, yana bosilsa to'xtatadi."""
        if self.process is not None:
            self.stop_translator()
        else:
            self.start_translator()

    def _sync_tiles(self) -> None:
        """Plitkalarni haqiqiy holatga moslaydi."""
        with suppress(Exception):
            running = self.process is not None
            self.tile_run.set_active(running)
            self.tile_run.set_text("Translating" if running else "Boshlash")
            self.tile_run.icon.setText(fluent_glyph(0xE71A, "■") if running else fluent_glyph(0xE768, "▶"))
            self.tile_quality.set_active(getattr(self, "quality_mode", False))

    @staticmethod
    def _tray_pixmap(size: int = 22, running: bool = False) -> QIcon:
        """Menyu paneli ikoni.

        `running=True` — tarjima ketayotganda ikon YASHIL bo'ladi
        (foydalanuvchi talabi: "tarjima yoqilganda soat oldidagi ikon
        yashil bo'lib tursin, o'chganda odatdagi holatida").

        Odatdagi holat — template ikon (qora + shaffof): macOS uni
        yorug'/qorong'i panelga o'zi moslashtiradi. Yashil holatda esa
        template O'CHIRILADI, aks holda tizim rangni bosib, yashil
        ko'rinmasdi.
        """
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#22c55e" if running else "#000000"))
        bars = (0.34, 0.62, 1.0, 0.62, 0.34)
        bar_width = size * 0.105
        gap = size * 0.075
        total = len(bars) * bar_width + (len(bars) - 1) * gap
        x = (size - total) / 2
        max_height = size * 0.62
        for factor in bars:
            height = max_height * factor
            painter.drawRoundedRect(
                QRectF(x, (size - height) / 2, bar_width, height),
                bar_width / 2,
                bar_width / 2,
            )
            x += bar_width + gap
        painter.end()
        icon = QIcon(pixmap)
        # Yashil holatda mask BO'LMASLIGI shart — aks holda tizim o'z rangini
        # qo'yib, yashil yo'qoladi.
        icon.setIsMask(not running)
        return icon

    def _build_tray(self) -> None:
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.tray_available:
            self.tray = None
            return
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._tray_pixmap())
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.tray_status_action = menu.addAction(t("Tayyor"))
        self.tray_status_action.setEnabled(False)
        menu.addSeparator()
        # Ilova faqat ikki tomonlama — tray'dan rejim va eski til menyulari
        # olib tashlandi (til asosiy oynadagi sodda panelda). Mavjud kod bilan
        # mos bo'lishi uchun ro'yxatlar bo'sh qoldiriladi.
        self.tray_mode_actions: list[QAction] = []
        self.tray_source_actions: list[QAction] = []
        self.tray_target_actions: list[QAction] = []
        # ASOSIY boshqaruv eng tepada: tray belgisidan boshlash/to'xtatish.
        self.tray_start_action = menu.addAction(t("Tarjimani boshlash"))
        # DIQQAT: tray'dan Start HAR DOIM ishlashi kerak — oyna yashirin
        # bo'lganda ham. Shuning uchun bevosita start_translator (u o'zi
        # qurilmalarni tekshiradi va kerak bo'lsa ro'yxatni yangilaydi).
        self.tray_start_action.triggered.connect(self.start_translator)
        self.tray_stop_action = menu.addAction(t("Tarjimani to‘xtatish"))
        self.tray_stop_action.triggered.connect(self.stop_translator)
        menu.addSeparator()
        # "Oynani ko'rsatish" O'RTADA turadi. Ilgari u eng tepadagi BOSILADIGAN
        # band edi: o'ng tugma bosilganda menyu kursor tagida ochilib, sichqoncha
        # qo'yib yuborilishi o'sha bandni bosib yuborardi — oyna "o'z-o'zidan"
        # ochilardi (foydalanuvchi shikoyati). Menyuning yuqori va quyi chekkasi
        # endi BOSILMAYDIGAN bandlar (holat va versiya) bilan himoyalangan.
        top_show_action = menu.addAction(t("Oynani ko‘rsatish"))
        top_show_action.triggered.connect(self._show_window)
        menu.addSeparator()
        # Oynani ochmasdan almashtirish uchun (foydalanuvchi ko'pincha
        # tray'dan boshqaradi). Oynadagi belgi bilan bir xil holatda turadi.
        self.tray_meeting_uz_action = menu.addAction("Meeting o‘zbekcha")
        self.tray_meeting_uz_action.setCheckable(True)
        self.tray_meeting_uz_action.setChecked(getattr(self, "meeting_uz", False))
        self.tray_meeting_uz_action.toggled.connect(self._toggle_meeting_uz)
        menu.addSeparator()
        # Sodda tray (foydalanuvchi talabi): monitor ("o‘zim ham eshitay"),
        # log yig‘ish (ZIP), mikrofon/karnay tiklash — olib tashlandi.
        open_logs_action = menu.addAction(t("Loglarni ochish"))
        open_logs_action.triggered.connect(self._open_logs_folder)
        send_logs_action = menu.addAction(t("Loglarni yuborish (yordam uchun)"))
        send_logs_action.triggered.connect(self.send_logs_to_support)
        pick_output_action = menu.addAction(t("Ovoz qurilmasini tanlash…"))
        pick_output_action.triggered.connect(lambda: self.ask_output_device(True))
        menu.addSeparator()
        # Interfeys tili — avtomatik aniqlanadi, shu yerdan o'zgartiriladi.
        self.tray_ui_lang_menu = menu.addMenu(t("Interfeys tili"))
        ui_lang_group = QActionGroup(self)
        ui_lang_group.setExclusive(True)
        self.tray_ui_lang_actions: list[QAction] = []
        for code in ui_i18n.SUPPORTED:
            action = self.tray_ui_lang_menu.addAction(ui_i18n.LANGUAGE_NAMES[code])
            action.setCheckable(True)
            action.setChecked(code == ui_i18n.current_language())
            ui_lang_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, c=code: self._change_ui_language(c)
            )
            self.tray_ui_lang_actions.append(action)
        settings_action = menu.addAction(t("Sozlamalar…"))
        settings_action.triggered.connect(self._tray_open_settings)
        quit_action = menu.addAction(t("Chiqish"))
        quit_action.triggered.connect(self._quit_from_tray)
        # Quyi chekka himoyasi: menyu YUQORIGA ochilganda (tepshoq pastda
        # bo'lsa — odatiy hol) kursor oxirgi band ustida qoladi. Bosilmaydigan
        # versiya yozuvi tasodifan "Chiqish"ni bosib yuborishning oldini oladi.
        menu.addSeparator()
        tray_version_action = menu.addAction(f"{APP_NAME} {APP_VERSION} · {APP_EDITION}")
        tray_version_action.setEnabled(False)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_source_selected(self, code: str) -> None:
        """Menyu paneldan manba tilini almashtirish."""
        if self.process is not None:
            self._sync_tray()
            return
        self._set_combo_code(self.source_language_select, code)

    def _tray_target_selected(self, code: str) -> None:
        """Menyu paneldan tarjima tilini almashtirish."""
        if self.process is not None:
            self._sync_tray()
            return
        self._set_combo_code(self.target_language_select, code)

    def _change_ui_language(self, code: str) -> None:
        """Interfeys tilini almashtiradi va menyuni qayta quradi."""
        ui_i18n.set_language(code)
        self.settings.setValue("ui/language", code)
        self.settings.sync()
        # Menyu bir marta quriladi — tilni yangilash uchun qaytadan quramiz.
        if self.tray is not None:
            self.tray.hide()
            self._build_tray()
        if self.tray is not None:
            self.tray.showMessage(
                APP_NAME,
                ui_i18n.LANGUAGE_NAMES[code],
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )

    def _tray_open_settings(self) -> None:
        # Oynani ochmasdan to'g'ridan-to'g'ri sozlama dialogini ko'rsatamiz
        # (menyu-panel rejimi: asosiy oyna umuman ochilmaydi).
        self.edit_settings()

    def _tray_activated(self, reason) -> None:  # noqa: ANN001
        """Tray belgisi bosilganda nima bo'ladi.

        Windows (2026-07-30 foydalanuvchi talabi): chap tugma — oyna xuddi
        Windows'ning tez sozlamalari (Quick Settings) kabi SOAT YONIDAN
        chiqadi va o'sha burchakka yopishib turadi; yana bosilsa yashirinadi.
        O'ng tugma — avvalgidek menyu (Qt buni kontekst-menyu orqali o'zi
        qiladi). macOS xatti-harakati o'zgarmaydi.
        """
        if platform.system() == "Windows":
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                if self.isVisible() and not self.isMinimized():
                    self.hide()
                else:
                    self._show_window()
            return
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ) and not self.isVisible():
            self._show_window()

    def _tray_mode_selected(self, index: int) -> None:
        if self.process is not None:
            # Rejim almashtirish yangi Gemini sessiyasini talab qiladi;
            # jonli tarjimani jimgina uzib yubormaymiz.
            self._sync_tray()
            if self.tray:
                self.tray.showMessage(
                    APP_NAME,
                    t("Rejimni almashtirish uchun avval tarjimani to‘xtating."),
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return
        self.direction.setCurrentIndex(index)

    def _minimize_window(self) -> None:
        """Oynani ko'zdan yashiradi (tarjima to'xtamaydi).

        Oyna Qt.Tool turida — macOS'da minimize qilingan Tool oynasi Dock'da
        ko'rinmaydi, ya'ni uni qaytarib ochib bo'lmasdi. Shu sabab menyu
        paneliga yashiramiz: tray > "Oynani ko'rsatish" bilan qaytadi.
        """
        tray = getattr(self, "tray", None)
        if tray is None:
            self.showMinimized()
            return
        self.hide()
        if not self.minimize_hint_shown:
            self.minimize_hint_shown = True
            pass  # bildirishnoma olib tashlandi (foydalanuvchi talabi)

    def _open_logs_folder(self) -> None:
        """Log papkasini ochadi (Finder/Explorer)."""
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def export_logs(self) -> None:
        """Barcha loglarni bitta ZIP qilib Desktop'ga chiqaradi.

        Foydalanuvchi shu faylni yuborsa, muammoni taxmin qilmasdan
        aniqlash mumkin: ilova jurnali + dvigatel jurnali + qurilmalar.
        """
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            desktop = Path.home() / "Desktop"
            target = (desktop if desktop.is_dir() else Path.home()) / (
                f"LiveTranslator-loglar-{stamp}.zip"
            )
            directory = log_directory()
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in ("app.log", "engine.log", "engine.prev.log"):
                    source = directory / name
                    if source.is_file():
                        archive.write(source, name)
                summary = [
                    f"{APP_NAME} {APP_VERSION}",
                    f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
                    f"Rejim: {self._current_mode()}",
                    f"API key kiritilgan: {'ha' if self.api_key else 'yo‘q'}",
                    f"Control URL: {self.control_url or '(yo‘q)'}",
                    f"Input: {self._device_name(self.input_device)}",
                    f"Output: {self._device_name(self.output_device)}",
                    f"Nazorat ovozi: {'yoqiq' if self.monitor_enabled else 'o‘chiq'}",
                    f"Oxirgi xato: {self.last_engine_error or '(yo‘q)'}",
                ]
                archive.writestr("holat.txt", "\n".join(summary))
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
            self._set_status("LOGLAR SAQLANDI", "#22c55e")
            self.route_hint.setText(f"Loglar: {target.name} (Desktop’da)")
            if self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    f"Loglar Desktop’ga saqlandi: {target.name}",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
        except Exception as error:
            self._set_status("LOGLARNI SAQLAB BO‘LMADI", "#ef4444")
            self.route_hint.setText(str(error)[:180])

    def _write_engine_command(self, **values: object) -> None:
        """Dvigatelga buyruq yozadi (mavjud `devices.json` kanali orqali).

        MUHIM: fayl ustiga yozilmaydi, BIRLASHTIRILADI — aks holda qurilma
        almashtirish buyrug'i pauza buyrug'ini o'chirib yuborardi."""
        try:
            current: dict = {}
            if self.device_state_path.exists():
                with suppress(Exception):
                    current = json.loads(
                        self.device_state_path.read_text(encoding="utf-8")
                    )
            if not isinstance(current, dict):
                current = {}
            current.update(values)
            self.device_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.device_state_path.write_text(
                json.dumps(current), encoding="utf-8"
            )
        except OSError as error:
            print(f"[UI] buyruq yozilmadi: {error}", flush=True)

    def _apply_meeting_uz_live(self, enabled: bool) -> None:
        """Rejimni JONLI almashtiradi — dasturni to'xtatmasdan.

        Ilgari buning uchun Stop→Start kerak edi = 7-10 soniya jimlik
        (ulanishning o'zi ~6 s). Endi faqat kiruvchi kanal to'xtaydi;
        chiquvchi kanal — sizning gapingiz tarjimasi — UZILMAYDI."""
        self._write_engine_command(incoming_paused=bool(enabled))
        if platform.system() != "Windows":
            return
        cable = getattr(self, "win_incoming_cable_match", "")
        target = "" if enabled else cable
        # PowerShell ~2 soniya oladi — GUI oqimida chaqirilsa ilova qotardi
        # (0.9.63 dagi jonli nosozlik). Fon oqimida bajaramiz.
        threading.Thread(
            target=self._meeting_uz_routing_worker,
            args=(enabled, target),
            daemon=True,
        ).start()

    def _meeting_uz_routing_worker(self, enabled: bool, cable: str) -> None:
        """Tizim CHIQISHINI almashtiradi (mikrofonga tegilmaydi)."""
        with self._routing_lock:
            if enabled:
                # Meeting ovozi kabelga emas, KARNAYGA qaytadi.
                wanted = ""
                for candidate in (
                    getattr(self, "preferred_output_name", ""),
                    getattr(self, "win_prev_render", ""),
                ):
                    if candidate and not is_virtual_device(candidate):
                        wanted = candidate
                        break
                out = (
                    self._win_audio("setrender", wanted)
                    if wanted
                    else self._win_audio("restorerender")
                )
                print(f"[ROUTING] meeting o'zbekcha: chiqish -> {out.strip()!r}", flush=True)
            elif cable:
                out = self._win_audio("setrender", cable)
                print(f"[ROUTING] tarjima qaytdi: chiqish -> {out.strip()!r}", flush=True)

    def _toggle_meeting_uz(self, enabled: bool) -> None:
        """«Meeting o'zbekcha» belgisi (oyna va tray bir-biriga mos turadi)."""
        if enabled == getattr(self, "meeting_uz", False):
            return
        if self.process is not None and not getattr(self, "engine_has_incoming", False):
            # Dastur allaqachon FAQAT-GAPIRISH rejimida ishga tushgan —
            # kiruvchi kanal umuman ochilmagan, uni yo'ldan qo'shib
            # bo'lmaydi. Bu yagona holat: Stop → Start kerak.
            self._sync_meeting_uz_widgets()
            if self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    "Kiruvchi tarjimani yoqish uchun tarjimani qayta ishga tushiring.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return
        self.meeting_uz = enabled
        self.settings.setValue("translation/meeting_uz", "true" if enabled else "false")
        self.settings.sync()
        self._sync_meeting_uz_widgets()
        print(f"[UI] Meeting o'zbekcha: {'YOQILDI' if enabled else 'o‘chirildi'}", flush=True)
        if self.process is not None:
            # JONLI almashtirish: dastur to'xtamaydi, gapingiz tarjimasi
            # uzilmaydi. Oynadagi panellarni ham qayta chizmaymiz —
            # meeting o'rtasida interfeys sakrab ketmasin.
            self._apply_meeting_uz_live(enabled)
            self.route_hint.setText(
                "🔊 Meeting o‘zbekcha: hammani jonli eshityapsiz. Gapingiz "
                "tarjimasi avvalgidek ketmoqda."
                if enabled
                else "🎧 Kiruvchi tarjima qayta yoqildi (bir necha soniya)."
            )
            self.route_hint.setVisible(True)
            return
        # Qurilmalar yangi rejimga moslanadi: gapirishda kirish = FIZIK
        # mikrofon, chiqish = virtual kabel (ikki tomonlamada aksincha).
        self._sync_mode_ui(apply_devices=True)
        self._sync_tray()
        self._set_controls(running=False)

    def _toggle_quality(self, enabled: bool) -> None:
        """«Sifatli tarjima» belgisi. Keyingi Start'dan kuchga kiradi."""
        if enabled == getattr(self, "quality_mode", False):
            return
        self.quality_mode = enabled
        self.settings.setValue("translation/quality", "true" if enabled else "false")
        self.settings.sync()
        print(f"[UI] Sifatli tarjima: {'YOQILDI' if enabled else 'o‘chirildi'}", flush=True)
        self._sync_tiles()
        if self.process is not None and self.tray is not None:
            # Rejim dvigatel ishga tushganda tanlanadi — jonli almashtirib
            # bo'lmaydi (butun tarjima zanjiri boshqacha quriladi).
            self.tray.showMessage(
                APP_NAME,
                "Sifatli tarjima keyingi ishga tushirishda qo‘llanadi.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def _sync_meeting_uz_widgets(self) -> None:
        """Oynadagi belgi va tray bandini haqiqiy holatga qaytaradi."""
        state = getattr(self, "meeting_uz", False)
        for widget in (
            getattr(self, "meeting_uz_check", None),
            getattr(self, "tray_meeting_uz_action", None),
        ):
            if widget is None:
                continue
            widget.blockSignals(True)
            widget.setChecked(state)
            widget.blockSignals(False)

    def _toggle_monitor(self, enabled: bool) -> None:
        self.monitor_enabled = enabled
        self.settings.setValue("audio/monitor_outgoing", "true" if enabled else "false")
        self.settings.sync()
        if self.process is not None and self.tray is not None:
            self.tray.showMessage(
                APP_NAME,
                t("Keyingi ishga tushirishda qo‘llanadi."),
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    _locked_position = None

    def moveEvent(self, event) -> None:  # noqa: ANN001
        """QULF: oyna QAYERGA siljitilmasin, burchakka qaytadi.

        0.9.88 da sichqoncha bilan surish olib tashlangan edi, lekin
        Windows'da oynani klaviatura (Win+strelkalar, Alt+Space → Ko'chirish)
        va boshqa tizim yo'llari bilan ham siljitish mumkin ekan
        (foydalanuvchi: "chetdan qimirlatib bo'lmaydigan qilish kerak").
        Endi har qanday siljishdan keyin oyna o'zi joyiga qaytadi."""
        super().moveEvent(event)
        if platform.system() != "Windows":
            return
        locked = self._locked_position
        if locked is None or self.pos() == locked:
            return
        # To'g'ridan-to'g'ri move() chaqirsak Windows hali surish rejimida
        # bo'ladi — keyingi aylanishda qaytaramiz (cheksiz halqa yo'q:
        # qaytgach pos == locked bo'ladi).
        QTimer.singleShot(0, lambda: self.move(locked))

    def _position_near_tray(self) -> None:
        """Oynani ekranning soat turadigan burchagiga yopishtiradi.

        Tray belgisining geometriyasi olinadi (qaysi ekranda ekanini bilish
        uchun); topilmasa asosiy ekran. Windows'da vazifa paneli odatda
        pastda — `availableGeometry` panelni chiqarib beradi, biz esa uning
        o'ng-pastki burchagiga 12 px chekinish bilan joylashamiz."""
        screen = None
        tray = getattr(self, "tray", None)
        if tray is not None:
            geometry = tray.geometry()
            if not geometry.isNull():
                screen = QApplication.screenAt(geometry.center())
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        target = QPoint(
            area.right() - self.width() - 12, area.bottom() - self.height() - 12
        )
        self._locked_position = target
        self.move(target)

    def _show_window(self) -> None:
        if platform.system() == "Windows":
            # Har ochilishda soat yonidagi burchakka qaytadi (foydalanuvchi
            # surib qo'ygan bo'lsa ham) — Quick Settings xatti-harakati.
            self._position_near_tray()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self.quit_requested = True
        self.close()
        QApplication.quit()

    def _sync_tray(self, running: bool | None = None, ready: bool = True) -> None:
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        active = self.process is not None if running is None else running
        current = self._current_mode()
        for action, mode in zip(self.tray_mode_actions, APP_MODES):
            action.setChecked(mode.code == current)
            action.setEnabled(not active)
        pair = self._current_pair()
        for action, language in zip(self.tray_source_actions, SOURCE_LANGUAGES):
            action.setChecked(language.code == pair.source)
            action.setEnabled(not active)
        for action, language in zip(self.tray_target_actions, TARGET_LANGUAGES):
            action.setChecked(language.code == pair.target)
            action.setEnabled(not active)
        self.tray_start_action.setEnabled(not active and ready)
        self.tray_stop_action.setEnabled(active)

    def _load_api_key(self) -> str:
        try:
            saved = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            if saved:
                return saved.strip()
        except Exception:
            pass
        if not getattr(sys, "frozen", False):
            dotenv = dotenv_values(PROJECT_DIR / ".env")
            candidate = str(
                dotenv.get("GOOGLE_API_KEY")
                or dotenv.get("GEMINI_API_KEY")
                or dotenv.get("EDCOM_API_KEY")
                or ""
            ).strip()
            if candidate:
                try:
                    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, candidate)
                except Exception:
                    pass
                return candidate
        return ""

    @staticmethod
    def _load_keyring(account: str) -> str:
        try:
            return (keyring.get_password(KEYRING_SERVICE, account) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _save_keyring(account: str, value: str) -> None:
        if value:
            keyring.set_password(KEYRING_SERVICE, account, value)
            return
        try:
            keyring.delete_password(KEYRING_SERVICE, account)
        except keyring.errors.PasswordDeleteError:
            pass

    @staticmethod
    def _default_control_url() -> str:
        configured = os.getenv("LIVE_TRANSLATOR_CONTROL_URL", "").strip()
        if configured:
            return configured
        if getattr(sys, "frozen", False):
            try:
                info_path = Path(sys.executable).resolve().parent.parent / "Info.plist"
                with info_path.open("rb") as info_file:
                    return str(
                        plistlib.load(info_file).get("LiveTranslatorControlURL", "")
                    ).strip()
            except (OSError, ValueError):
                pass
        return ""

    def _load_or_create_device_id(self) -> str:
        current = self._load_keyring(KEYRING_DEVICE_ACCOUNT)
        if current:
            return current
        current = str(uuid.uuid4())
        try:
            self._save_keyring(KEYRING_DEVICE_ACCOUNT, current)
        except Exception:
            pass
        return current

    def _first_run(self) -> None:
        if not self.api_key:
            self.edit_settings(required=True)
        if not self._has_base_cable(refresh=True):
            QTimer.singleShot(300, self._begin_first_run_driver_setup)

    def _has_base_cable(self, refresh: bool = False) -> bool:
        """ASOSIY kabel (VB-CABLE / BlackHole 2ch) borligini tekshiradi.

        DIQQAT: shunchaki 'biror virtual kabel bormi' emas — aynan asosiy
        kabel. Aks holda faqat Hi-Fi bor mashinada (real holat) ilova
        'kabel bor' deb asosiy VB-CABLE'ni o'rnatishni o'tkazib yuborardi.
        """
        base = "vb-cable" if platform.system() == "Windows" else "blackhole 2ch"
        for name in self._virtual_driver_names(refresh=refresh):
            if base in virtual_device_family(name):
                return True
        return False

    def _begin_first_run_driver_setup(self) -> None:
        if self.driver_install_prompted or self._has_base_cable(refresh=True):
            return
        self.driver_install_prompted = True
        self.driver_variant = "2ch"  # birinchi ochilishda ASOSIY kabel
        self._set_status("AUDIO DRIVER O‘RNATILMOQDA…", "#f59e0b")
        self.install_driver()

    def edit_settings(self, _checked: bool = False, required: bool = False) -> None:
        dialog = SettingsDialog(self, self.api_key, self.control_url, self.license_key)
        # Ilova Dock'da ko'rinmaydi (menyu-panel rejimi) — dialog o'zi
        # oldinga chiqmasa, boshqa oynalar ortida ko'rinmay qolardi.
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            if required:
                self._set_status("API KEY KERAK", "#ef4444")
            return
        try:
            control_url = validate_control_url(dialog.control_url)
            self._save_keyring(KEYRING_ACCOUNT, dialog.api_key)
            self._save_keyring(KEYRING_CONTROL_URL_ACCOUNT, control_url)
            self._save_keyring(KEYRING_LICENSE_ACCOUNT, dialog.license_key)
        except (Exception, LicenseError) as error:
            QMessageBox.critical(self, "Keychain xatosi", str(error))
            return
        self.api_key = dialog.api_key
        self.control_url = control_url
        self.license_key = dialog.license_key
        self.license_client = None
        if not self.api_key:
            self._set_status("API KEY KERAK", "#ef4444")
            self._set_controls(running=False)
            return
        self._set_status("TAYYOR", "#94a3b8")
        self._set_controls(running=False)

    @staticmethod
    def _virtual_driver_names(refresh: bool = False) -> list[str]:
        system = platform.system()
        try:
            if refresh:
                # PortAudio caches the device list. A driver installed while
                # the app is open otherwise stays invisible until relaunch.
                sd._terminate()  # type: ignore[attr-defined]
                sd._initialize()  # type: ignore[attr-defined]
            names: list[str] = []
            for device in sd.query_devices():
                name = str(device["name"])
                if not is_virtual_device(name):
                    continue
                if int(device["max_input_channels"]) <= 0 and int(device["max_output_channels"]) <= 0:
                    continue
                if name not in names:
                    names.append(name)
            if system == "Windows":
                return names
            return sorted(names, key=lambda name: ("blackhole 2ch" not in name.casefold(), name))
        except Exception:
            return []

    @classmethod
    def _virtual_driver_name(cls, refresh: bool = False) -> str | None:
        names = cls._virtual_driver_names(refresh)
        return names[0] if names else None

    @staticmethod
    def _device_name(combo: QComboBox) -> str:
        return str(combo.currentData(Qt.ItemDataRole.UserRole + 1) or "")

    @staticmethod
    def _populate_devices(
        combo: QComboBox,
        choices,  # noqa: ANN001
        preferred_index: int | None = None,
    ) -> None:
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        selected = -1
        for position, choice in enumerate(choices):
            combo.addItem(choice.name, choice.index)
            combo.setItemData(
                position, choice.name, Qt.ItemDataRole.UserRole + 1
            )
            if choice.index == previous or (previous is None and choice.index == preferred_index):
                selected = position
        if selected >= 0:
            combo.setCurrentIndex(selected)
        combo.blockSignals(False)

    def _refresh_audio_devices(self) -> None:
        if not hasattr(self, "input_device") or self.process is not None:
            return
        try:
            inputs = available_devices("input")
            outputs = available_devices("output")
            try:
                preferred_input = auto_input_device(None).index
            except RuntimeError:
                preferred_input = int(sd.default.device[0])
            try:
                preferred_output = auto_output_device(None).index
            except RuntimeError:
                preferred_output = int(sd.default.device[1])
            self._populate_devices(self.input_device, inputs, preferred_input)
            self._populate_devices(self.output_device, outputs, preferred_output)
            self._populate_devices(self.duplex_outgoing_input, inputs, preferred_input)
            self._populate_devices(self.duplex_outgoing_output, outputs, preferred_output)
            self.audio_devices_initialized = True
            # To'liq avtomatik rejim: ro'yxat har yangilanganda (AirPods
            # ulandi/uzildi va h.k.) yo'nalish presetlari qayta qo'llanadi —
            # foydalanuvchi hech qachon qo'lda tanlashi shart emas.
            self._apply_direction_devices(self._current_mode())
        except Exception as error:
            # Ilgari bu faqat ekranda ko'rinardi — log orqali tashxis qilib
            # bo'lmasdi. Endi logda ham qoladi.
            print(f"[UI] Audio qurilmalar o'qilmadi: {error!r}", flush=True)
            self.route_hint.setText(f"Audio qurilmalar o‘qilmadi: {error}")

    def _audio_route_changed(self) -> None:
        if not hasattr(self, "route_hint"):
            return
        input_name = self._device_name(self.input_device)
        output_name = self._device_name(self.output_device)
        virtual_input = is_virtual_device(input_name)
        virtual_output = is_virtual_device(output_name)
        if self._current_mode() == "duplex":
            try:
                routes = self._duplex_routes()
                validate_duplex_routes(routes)
                # Sariq ogohlantirish olib tashlandi (foydalanuvchi talabi) —
                # qurilmalarni ilova o'zi avtomatik sozlaydi.
                self.route_hint.setText("")
            except (TypeError, ValueError) as error:
                self.route_hint.setText(str(error))
            if hasattr(self, "start_button"):
                self._set_controls(running=self.process is not None)
            return
        if virtual_output and not virtual_input:
            meeting_microphone = (
                "CABLE Output (VB-Audio Virtual Cable)"
                if "cable input" in output_name.casefold()
                else output_name
            )
            pair = self._current_pair()
            self.route_hint.setText(
                f"Siz {language_caption(pair.source)} gapirasiz — meetingdagilar "
                f"{language_caption(pair.target)} eshitadi. Zoom/Meet mikrofoni: "
                f"“Same as System” (avtomatik) yoki “{meeting_microphone}”. "
                "Agar ular sizning xom ovozingizni eshitsa — Zoom shu ikkisidan "
                "birini tanlamagan."
            )
        elif virtual_input and not virtual_output:
            self.route_hint.setText(
                "Kompyuter va meeting ovozi avtomatik olinib, tarjima "
                "tanlangan speaker orqali eshittiriladi."
            )
        elif input_name and output_name:
            self.route_hint.setText("Tanlangan ovoz tarjima qilinib chiqish qurilmasiga uzatiladi.")
        else:
            self.route_hint.setText("Input va output audio qurilmasini tanlang.")
        if hasattr(self, "start_button"):
            self._set_controls(running=self.process is not None)

    @staticmethod
    def _endpoint_from_combo(combo: QComboBox) -> AudioEndpoint:
        device_index = combo.currentData()
        if device_index is None:
            # Eng ko'p uchraydigan sabab: ikkinchi virtual kabel o'rnatilmagan
            # (drayver o'rnatishda UAC rad etilgan yoki qayta yoqilmagan).
            raise ValueError(
                "Kerakli audio qurilma topilmadi. Virtual audio kabellar "
                "o‘rnatilganini tekshiring («Audio Driver» tugmasi) va "
                "kompyuterni qayta yoqing."
            )
        return AudioEndpoint(int(device_index), TranslatorWindow._device_name(combo))

    def _duplex_routes(self) -> DuplexRoutes:
        return DuplexRoutes(
            incoming_input=self._endpoint_from_combo(self.input_device),
            incoming_output=self._endpoint_from_combo(self.output_device),
            outgoing_input=self._endpoint_from_combo(self.duplex_outgoing_input),
            outgoing_output=self._endpoint_from_combo(self.duplex_outgoing_output),
        )

    @staticmethod
    def _select_device_kind(
        combo: QComboBox,
        virtual: bool,
        preferred_words: tuple[str, ...] = (),
        avoid_words: tuple[str, ...] = (),
    ) -> bool:
        """avoid_words: nomida shu so'z bor qurilma FAQAT boshqa nomzod
        qolmagandagina tanlanadi (masalan Bluetooth hands-free mikrofoni —
        u tanlansa naushnik telefon-sifat rejimiga tushib, ham yozish, ham
        ijro buziladi)."""
        candidates: list[tuple[int, str]] = []
        for index in range(combo.count()):
            name = str(
                combo.itemData(index, Qt.ItemDataRole.UserRole + 1) or ""
            ).casefold()
            is_virtual = is_virtual_device(name)
            if is_virtual == virtual:
                candidates.append((index, name))
        if avoid_words:
            avoided = [
                c for c in candidates if any(w in c[1] for w in avoid_words)
            ]
            candidates = [
                c for c in candidates if not any(w in c[1] for w in avoid_words)
            ] + avoided
        for word in preferred_words:
            for index, name in candidates:
                if word in name:
                    combo.setCurrentIndex(index)
                    return True
        if candidates:
            combo.setCurrentIndex(candidates[0][0])
            return True
        return False

    @staticmethod
    def _select_distinct_virtual(
        combo: QComboBox,
        excluded_index: int | None,
        excluded_name: str,
        preferred_words: tuple[str, ...],
    ) -> bool:
        candidates: list[tuple[int, str, int]] = []
        for position in range(combo.count()):
            name = str(
                combo.itemData(position, Qt.ItemDataRole.UserRole + 1) or ""
            )
            device_index = combo.itemData(position)
            if (
                device_index is not None
                and int(device_index) != excluded_index
                and is_virtual_device(name)
                and virtual_device_family(name)
                != virtual_device_family(excluded_name)
            ):
                candidates.append((position, name.casefold(), int(device_index)))
        for word in preferred_words:
            for position, name, _device_index in candidates:
                if word in name:
                    combo.setCurrentIndex(position)
                    return True
        if candidates:
            combo.setCurrentIndex(candidates[0][0])
            return True
        combo.setCurrentIndex(-1)
        return False

    def _restore_preferred_output(self) -> bool:
        """Foydalanuvchi «ESHITAMAN» dan tanlagan qurilmani qaytaradi.

        JONLI NOSOZLIK: foydalanuvchi "PC Monitor" ni tanlaganda ham ilova
        uni "Динамики (Realtek)" ga QAYTARIB yuborardi — chunki qurilma
        ro'yxati yangilanganda (`_apply_direction_devices`) chiqish combo'si
        AVTOMATIK tanlov bilan qayta yozilardi va foydalanuvchi tanlovi
        e'tiborsiz qolardi. Endi saqlangan tanlov HAR DOIM ustun."""
        wanted = str(getattr(self, "preferred_output_name", "") or "")
        if not wanted:
            return False
        for index in range(self.output_device.count()):
            name = str(
                self.output_device.itemData(index, Qt.ItemDataRole.UserRole + 1) or ""
            )
            if name == wanted:
                self.output_device.setCurrentIndex(index)
                return True
        return False

    def _restore_saved_cable(self, combo: QComboBox, key: str) -> bool:
        """Oldingi sessiyada ishlatilgan kabelni qaytaradi (agar hali mavjud).

        NEGA KERAK: kompyuterda ikkita Hi-Fi kabel bo'lishi mumkin
        ("Hi-Fi Cable" va "2- Hi-Fi Cable"). Avtomatik tanlov qurilmalar
        ro'yxati tartibiga bog'liq — monitor/webcam ulanganda tartib
        o'zgarib, ilova BOSHQA kabelni tanlab qolardi. Meet esa eskisiga
        qadab qo'yilgan bo'lsa, ovoz o'tmay qolardi (jonli nosozlik:
        "til o'zgartirgandim, endi ikkala tomon ham eshitilmayapti").
        Shuning uchun tanlov ESLAB QOLINADI va imkon qadar o'zgarmaydi."""
        saved = str(self.settings.value(key, "") or "")
        if not saved:
            return False
        for index in range(combo.count()):
            name = str(combo.itemData(index, Qt.ItemDataRole.UserRole + 1) or "")
            if name == saved:
                combo.setCurrentIndex(index)
                return True
        return False

    def _remember_cables(self) -> None:
        """Tanlangan kabellarni keyingi ochilish uchun saqlaydi."""
        with suppress(Exception):
            self.settings.setValue(
                "cables/incoming_input", self._device_name(self.input_device)
            )
            self.settings.setValue(
                "cables/outgoing_output",
                self._device_name(self.duplex_outgoing_output),
            )

    def _apply_direction_devices(self, mode: str) -> None:
        if not hasattr(self, "input_device") or self.process is not None:
            return
        if mode in {"incoming", "duplex"}:
            if not self._restore_saved_cable(
                self.input_device, "cables/incoming_input"
            ):
                # Hi-Fi Cable'ni BIRINCHI afzal ko'ramiz: real mashinada
                # meeting audiosi Hi-Fi kabelga ("Hi-Fi Cable Output (2-…)")
                # ketadi, shunda Start'da tizim chiqishi "Динамики (2- VB-Audio
                # Hi-Fi Cable)"ga o'rnatiladi va ilova audioni ushlaydi. Hi-Fi
                # bo'lmasa oddiy "cable output"ga tushamiz (zaxira).
                self._select_device_kind(
                    self.input_device,
                    virtual=True,
                    preferred_words=(
                        "blackhole 2ch",
                        "hi-fi cable output",
                        "vb-audio hi-fi",
                        "cable output",
                    ),
                )
            # Foydalanuvchi tanlovi BIRINCHI (aks holda uni qayta yozib
            # yuborardik — "PC Monitor tanlasam yana dinamikka qaytyapti").
            if not self._restore_preferred_output():
                self._select_device_kind(
                    self.output_device,
                    virtual=False,
                    # Birinchi navbatda foydalanuvchi HOZIR eshitayotgan
                    # qurilma (tizim tanlovi) — nomi "P2961" kabi notanish
                    # bo'lsa ham topiladi. Nom bo'yicha tanlash zaxira yo'l.
                    preferred_words=self._output_preference_words(),
                )
        else:
            self._select_device_kind(
                self.input_device,
                virtual=False,
                # Kirill nomlar ham ("Микрофон (USB2.0 Camera)") — rus Windows'da
                # webcam mikrofoni lotin so'zlarga mos kelmay, tasodifiy qurilma
                # (ko'pincha BT hands-free) tanlanib gapirish o'lardi.
                preferred_words=(
                    "macbook air microphone", "microphone", "микрофон",
                    "webcam", "web cam", "camera", "камер", "usb", "mic",
                ),
                avoid_words=("hands-free", "handsfree", "headset", "гарнитур"),
            )
            self._select_device_kind(
                self.output_device,
                virtual=True,
                # GAPIRISH chiqishi VIRTUAL kabel bo'lishi SHART — tarjima
                # kabelga, undan Zoom mikrofoniga ketadi (foydalanuvchi o'zi
                # ESHITMAYDI). Faqat Hi-Fi Cable o'rnatilgan mashinada chiqish
                # uning IJRO tomoni bo'ladi ("Speakers/Динамики (...Hi-Fi
                # Cable)") — shu nomlar ham preferred, aks holda fizik karnay
                # tanlanib qolib tarjima o'ziga qaytardi (real regressiya).
                preferred_words=(
                    "blackhole 2ch",
                    "cable input",
                    "hi-fi cable",
                    "vb-audio hi-fi",
                ),
            )
        if mode == "duplex":
            self._select_device_kind(
                self.duplex_outgoing_input,
                virtual=False,
                # Yuqoridagi kabi: kirill/webcam nomlari + BT hands-free'dan
                # qochish (aks holda naushnik HFP rejimga tushib ovoz buziladi).
                preferred_words=(
                    "macbook air microphone", "microphone", "микрофон",
                    "webcam", "web cam", "camera", "камер", "usb", "mic",
                ),
                avoid_words=("hands-free", "handsfree", "headset", "гарнитур"),
            )
            incoming_virtual_id = self.input_device.currentData()
            # Chiquvchi kabel ham ESLAB QOLINADI (kiruvchi kabel bilan bir xil
            # oiladan bo'lib qolmasligi shart — aks holda tarjima o'z-o'ziga
            # qaytadi). Mos kelmasa avtomatik tanlovga tushamiz.
            restored_out = self._restore_saved_cable(
                self.duplex_outgoing_output, "cables/outgoing_output"
            )
            if restored_out and virtual_device_family(
                self._device_name(self.duplex_outgoing_output)
            ) == virtual_device_family(self._device_name(self.input_device)):
                restored_out = False
            if not restored_out:
                self._select_distinct_virtual(
                    self.duplex_outgoing_output,
                    int(incoming_virtual_id) if incoming_virtual_id is not None else None,
                    self._device_name(self.input_device),
                    preferred_words=(
                        "blackhole 16ch",
                        "blackhole 64ch",
                        # Windows: kiruvchi kanal Hi-Fi kabelni oldi, chiquvchi
                        # esa base VB-CABLE ("CABLE Input") — ishonchli IKKINCHI
                        # kabel. 1-Hi-Fi nusxa ba'zan buzuq/faol emas
                        # (PortAudio -9996, setcapture NOT_FOUND).
                        "cable input",
                        "hi-fi cable",
                        "vb-audio hi-fi",
                        "cable-b input",
                        "cable-a input",
                    ),
                )
            self._remember_cables()
        self._audio_route_changed()

    def _refresh_driver_state(self) -> None:
        """Drayverlar ro'yxatini FON oqimida yangilaydi (GUI qotmasin)."""
        if self._driver_scan_busy:
            return
        self._driver_scan_busy = True
        refresh = self.process is None
        threading.Thread(
            target=self._scan_drivers_worker, args=(refresh,), daemon=True
        ).start()

    def _scan_drivers_worker(self, refresh: bool) -> None:
        try:
            with self._sd_lock:
                drivers = self._virtual_driver_names(refresh=refresh)
        except Exception:
            drivers = []
        self.device_scan_signals.drivers.emit(list(drivers))

    def _apply_driver_state(self, drivers: list) -> None:
        self._driver_scan_busy = False
        driver = drivers[0] if drivers else None
        virtual_families = {virtual_device_family(name) for name in drivers}
        is_windows = platform.system() == "Windows"
        base_family = "vb-cable" if is_windows else "blackhole 2ch"
        have_base = any(base_family in fam for fam in virtual_families)
        duplex_missing = self._current_mode() == "duplex" and len(virtual_families) < 2
        # DIQQAT: agar ASOSIY kabel (VB-CABLE / BlackHole 2ch) umuman yo'q
        # bo'lsa — duplex bo'lsa ham AVVAL uni o'rnatamiz. Aks holda ilova
        # to'g'ridan-to'g'ri ikkinchi kabelni o'rnatib, asosiysini o'tkazib
        # yuborardi (real Windows testda aynan shunday bo'ldi).
        if duplex_missing and have_base:
            self.driver_variant = "16ch" if not is_windows else "second"
        else:
            self.driver_variant = "2ch"
        self.driver_row.setVisible(driver is None or duplex_missing)
        if duplex_missing:
            if platform.system() == "Darwin" and BLACKHOLE_16CH_DRIVER_PATH.exists():
                self.driver_label.setText(
                    "BlackHole 16ch o‘rnatilgan, lekin CoreAudio hali yuklamagan. "
                    "Mac’ni qayta ishga tushiring."
                )
                self.driver_button.setText("BLACKHOLE 16CH QAYTA O‘RNATISH")
                if self.process is None:
                    self._set_status("MAC’NI RESTART QILING", "#f59e0b")
            elif platform.system() == "Darwin":
                self.driver_label.setText(
                    "IKKALASI rejimi uchun ikkinchi mustaqil yo‘l — BlackHole 16ch kerak."
                )
                self.driver_button.setText("BLACKHOLE 16CH O‘RNATISH")
            else:
                self.driver_label.setText(
                    "IKKALASI rejimi uchun ikkinchi mustaqil virtual audio cable kerak."
                )
                self.driver_button.setText("IKKINCHI AUDIO CABLE KERAK")
        elif driver:
            self.driver_label.setText(f"✓ {driver}")
            self.driver_button.setText("AUDIO DRIVER O‘RNATISH")
        elif platform.system() == "Darwin" and BLACKHOLE_DRIVER_PATH.exists():
            self.driver_label.setText(
                "BlackHole o‘rnatilgan, lekin CoreAudio hali yuklamagan. "
                "Mac’ni qayta ishga tushiring."
            )
            self.driver_button.setText("BLACKHOLE QAYTA O‘RNATISH")
            if self.process is None:
                self._set_status("MAC’NI RESTART QILING", "#f59e0b")
        else:
            name = "VB-CABLE" if platform.system() == "Windows" else "BlackHole 2ch"
            self.driver_label.setText(f"{name} topilmadi. Birinchi marta o‘rnatish kerak.")
            self.driver_button.setText("AUDIO DRIVER O‘RNATISH")
        self._refresh_audio_devices()
        if self._current_mode() == "duplex" and self.process is None:
            try:
                validate_duplex_routes(self._duplex_routes())
            except (TypeError, ValueError):
                self._apply_direction_devices("duplex")
        self._set_controls(running=self.process is not None)

    def install_driver(self) -> None:
        # Windows'da ikkinchi kabel (duplex) ham AVTOMATIK o'rnatiladi —
        # bepul Hi-Fi Cable. Veb-saytga yo'naltirmaymiz.
        self.driver_button.setEnabled(False)
        self.driver_button.setText("YUKLANMOQDA…")
        threading.Thread(target=self._download_driver, daemon=True).start()

    def _download_driver(self) -> None:
        try:
            if platform.system() == "Darwin":
                if self.driver_variant == "16ch":
                    path = Path(tempfile.gettempdir()) / "BlackHole16ch-0.7.1.pkg"
                    self._download_verified(
                        BLACKHOLE_16CH_URL, path, BLACKHOLE_16CH_SHA256
                    )
                else:
                    path = Path(tempfile.gettempdir()) / "BlackHole2ch-0.7.1.pkg"
                    self._download_verified(BLACKHOLE_URL, path, BLACKHOLE_SHA256)
                self.driver_signals.ready.emit(str(path))
                return
            if platform.system() == "Windows":
                is64 = sys.maxsize > 2**32
                if self.driver_variant == "second":
                    # Duplex'ning ikkinchi kabeli — bepul Hi-Fi Cable.
                    archive = Path(tempfile.gettempdir()) / "HiFiCable.zip"
                    folder = Path(tempfile.gettempdir()) / "LiveTranslator-HiFiCable"
                    self._obtain_driver_archive(
                        "HiFiCableAsioBridgeSetup_v1007.zip",
                        HIFI_CABLE_URL, HIFI_CABLE_SHA256, archive,
                    )
                    shutil.rmtree(folder, ignore_errors=True)
                    folder.mkdir(parents=True)
                    with zipfile.ZipFile(archive) as package:
                        package.extractall(folder)
                    candidates = [
                        folder / "HiFiCableAsioBridgeSetup_x64.exe",
                        folder / "HiFiCableAsioBridgeSetup.exe",
                    ]
                    setup = next((c for c in candidates if c.exists()), None)
                    if setup is None:
                        found = list(folder.rglob("*Setup*.exe"))
                        setup = found[0] if found else candidates[-1]
                else:
                    archive = Path(tempfile.gettempdir()) / "VBCABLE_Driver_Pack45.zip"
                    folder = Path(tempfile.gettempdir()) / "LiveTranslator-VBCABLE"
                    self._obtain_driver_archive(
                        "VBCABLE_Driver_Pack45.zip",
                        VBCABLE_URL, VBCABLE_SHA256, archive,
                    )
                    shutil.rmtree(folder, ignore_errors=True)
                    folder.mkdir(parents=True)
                    with zipfile.ZipFile(archive) as package:
                        package.extractall(folder)
                    setup = folder / ("VBCABLE_Setup_x64.exe" if is64 else "VBCABLE_Setup.exe")
                import ctypes

                # "-i -h": VB-Audio'ning jimgina o'rnatish rejimi — foydalanuvchi
                # setup oynasida hech narsa bosmaydi, faqat bitta UAC so'raladi.
                result = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", str(setup), "-i -h", str(folder), 0
                )
                if result <= 32:
                    # Jim rejim ishlamasa (eski pack), oddiy oynani ochamiz.
                    result = ctypes.windll.shell32.ShellExecuteW(
                        None, "runas", str(setup), None, str(folder), 1
                    )
                if result <= 32:
                    raise RuntimeError(f"VB-CABLE setup ochilmadi: {result}")
                self.driver_signals.ready.emit("")
                return
            raise RuntimeError("Bu operatsion tizim hozircha qo‘llanmaydi.")
        except Exception as error:
            self.driver_signals.failed.emit(str(error))

    @staticmethod
    def _download_verified(url: str, path: Path, expected_sha256: str) -> None:
        def fetch(context) -> None:  # noqa: ANN001
            with urllib.request.urlopen(
                url, timeout=60, context=context
            ) as response, path.open("wb") as output:
                shutil.copyfileobj(response, output)

        try:
            fetch(secure_ssl_context())
        except Exception as error:
            # Korporativ tarmoqlarda TLS trafik tekshiriladi va sertifikatni
            # kompaniyaning O'Z ildiz sertifikati imzolaydi — u certifi
            # to'plamida yo'q, lekin Windows sertifikat do'konida bor
            # (CERTIFICATE_VERIFY_FAILED: unable to get local issuer).
            # Shu sabab tizim do'koni bilan qayta urinamiz.
            if "CERTIFICATE" not in str(error).upper():
                raise
            print(f"[DRIVER] certifi bilan bo‘lmadi ({error}); tizim CA bilan urinamiz", flush=True)
            fetch(ssl.create_default_context())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            path.unlink(missing_ok=True)
            raise RuntimeError("Driver checksum mos kelmadi; o‘rnatish bekor qilindi.")

    @staticmethod
    def _bundled_driver(filename: str, expected_sha256: str) -> Path | None:
        """Ilova ICHIGA qo'shilgan drayver arxivi (internet kerak emas).

        Yuklab olish korporativ tarmoqda SSL sertifikat xatosi bilan
        yiqilardi ("unable to get local issuer certificate") va drayver
        umuman o'rnatilmasdi. Arxiv endi ilova bilan birga keladi."""
        candidate = resource_path(f"drivers/{filename}")
        try:
            if not candidate.exists():
                return None
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest != expected_sha256:
                print(f"[DRIVER] ichki nusxa buzuq: {filename}", flush=True)
                return None
            return candidate
        except Exception as error:
            print(f"[DRIVER] ichki nusxa o‘qilmadi: {error}", flush=True)
            return None

    def _obtain_driver_archive(
        self, filename: str, url: str, expected_sha256: str, target: Path
    ) -> None:
        """Drayver arxivini tayyorlaydi: avval ILOVA ICHIDAN, bo'lmasa yuklab."""
        bundled = self._bundled_driver(filename, expected_sha256)
        if bundled is not None:
            shutil.copyfile(bundled, target)
            print(f"[DRIVER] ichki nusxadan olindi: {filename}", flush=True)
            return
        print(f"[DRIVER] internetdan yuklanmoqda: {url}", flush=True)
        self._download_verified(url, target, expected_sha256)

    def _restore_speaker_clicked(self) -> None:
        result = self.restore_windows_default_speaker()
        if result.startswith("OK:"):
            self._set_status("KARNAY TIKLANDI", "#22c55e")
            if self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    f"Ovoz endi: {result[3:].strip()}",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )
        elif self.tray is not None:
            pass  # bildirishnoma olib tashlandi (foydalanuvchi talabi)

    def _win_audio(self, action: str, name: str = "") -> str:
        """audio_config.ps1 ni chaqiradi (Windows default qurilma boshqaruvi)."""
        if platform.system() != "Windows":
            return ""
        script = resource_path("audio_config.ps1")
        if not script.exists():
            return ""
        try:
            args = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Action",
                action,
            ]
            if name:
                args += ["-Name", name]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # UTF-8 bilan o'qiymiz (text=True lokal cp1251 ishlatib, ps1 ning
            # UTF-8 chiqishidagi kirill qurilma nomlarini — masalan "Наушники
            # (P2961)" — buzardi -> dvigatel qurilmani topolmasdi).
            result = subprocess.run(
                args, capture_output=True, encoding="utf-8", errors="replace",
                # 30 -> 90 s. Jonli nosozlik (2026-07-29, uy kompyuteri):
                # `startduplex` 30 soniyada uzilib, yo'naltirish UMUMAN
                # qo'llanmagan. Sekin kompyuterda yoki antivirus PowerShell'ni
                # tekshirayotganda C# kompilyatsiya uzoq ketadi. Bu chaqiruv
                # FON oqimida — kutish GUI'ni qotirmaydi.
                timeout=90, creationflags=creationflags,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            # Diagnostika: har bir routing amali app.log'ga yoziladi (Start
            # va Stop'da aynan nima o'zgarayotganini ko'rish uchun).
            print(
                f"[ROUTING] {action} name={name!r} rc={result.returncode} "
                f"out={out!r}" + (f" err={err[:200]!r}" if err else ""),
                flush=True,
            )
            return out
        except Exception as error:
            print(f"[ROUTING] {action} name={name!r} EXCEPTION: {error}", flush=True)
            return ""

    @staticmethod
    def _win_cable_match(device_name: str) -> str:
        """Qurilma nomidan tizim-default uchun ANIQ nusxa tokeni.

        "hifi:N" / "vbcable:N" — N nusxa raqami (raqamsiz = 1). audio_config.ps1
        buni o'sha oiladagi ANIQ nusxaga (masalan "2- VB-Audio Hi-Fi Cable")
        moslaydi. Ilgari "hi-fi" degan qo'pol so'z ikkita Hi-Fi Cable'ni
        ajrata olmasdi — ilova bir kabeldan o'qib, tizim boshqasiga
        yo'naltirilib, tinglash umuman ishlamasdi."""
        fam = virtual_device_family(device_name)
        if fam.startswith("vb-hifi-cable"):
            inst = fam.rsplit("-", 1)[-1] if fam[-1].isdigit() else "1"
            return f"hifi:{inst}"
        if fam.startswith("vb-cable"):
            inst = fam.rsplit("-", 1)[-1] if fam[-1].isdigit() else "1"
            return f"vbcable:{inst}"
        return device_name

    def _win_apply_routing(self, render_match: str, capture_match: str) -> None:
        """Start'da: joriy default'larni SAQLAB, kabellarga o'tkazadi.

        Shu tufayli Zoom/Meet 'Same as System' bilan hech narsa tanlamasdan
        to'g'ri ishlaydi — foydalanuvchi qurilmaga tegmaydi.
        """
        if platform.system() != "Windows":
            return
        # ENG TEZ YO'L: hozirgi default'larni ctypes bilan o'qiymiz (~10 ms),
        # kabellarga o'tkazishni esa FON oqimida bajaramiz. Ilgari bularning
        # hammasi GUI oqimida PowerShell orqali bo'lardi (~2-3 s) va Start
        # bosilganda ilova qotardi.
        if platform.system() == "Windows":
            try:
                from winaec import default_endpoint_name

                fast_render = default_endpoint_name(0)
                fast_capture = default_endpoint_name(1)
            except Exception as error:
                print(f"[ROUTING] tez o'qish xato: {error}", flush=True)
                fast_render = fast_capture = ""
            if fast_render or fast_capture:
                self.win_prev_render = fast_render
                self.win_prev_capture = fast_capture
                self.win_meeting_speaker = ""
                self.win_meeting_mic = ""
                self.win_physical_output = ""
                threading.Thread(
                    target=self._apply_routing_worker,
                    args=(render_match, capture_match),
                    daemon=True,
                ).start()
                return
        # TEZKOR YO'L: hammasi BITTA PowerShell chaqiruvida. Har chaqiruv C#
        # kompilyatsiya qilgani uchun ~2 soniya ketardi va Start bosilganda
        # ilova bir necha soniya QOTIB qolardi (foydalanuvchi shikoyati:
        # "bosilmay qolyapti"). Eski ko'p-chaqiruvli yo'l zaxira sifatida
        # qoladi (eski ps1 bilan ham ishlashi uchun).
        combined = self._win_audio(
            "startduplex", f"{render_match}|{capture_match}"
        )
        values: dict[str, str] = {}
        for line in combined.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        if "prevrender" in values or "prevcapture" in values:
            self.win_prev_render = values.get("prevrender", "")
            self.win_prev_capture = values.get("prevcapture", "")
            self.win_meeting_speaker = values.get("render", "")
            self.win_meeting_mic = values.get("capture", "")
            self.win_physical_output = values.get("physical", "")
            return
        # --- Zaxira: eski, ko'p chaqiruvli yo'l ---
        out = self._win_audio("getdefaults")
        for line in out.splitlines():
            if line.startswith("render="):
                self.win_prev_render = line[len("render="):].strip()
            elif line.startswith("capture="):
                self.win_prev_capture = line[len("capture="):].strip()
        if render_match:
            out_render = self._win_audio("setrender", render_match)
            # Meet/Zoom KARNAYI aynan shu kabel bo'lishi kerak — aks holda
            # meeting ovozi ilovaga kelmaydi (suhbatdosh gapirsa eshitilmaydi).
            self.win_meeting_speaker = (
                out_render.split("OK:", 1)[1].strip()
                if out_render.startswith("OK:") else ""
            )
        if capture_match:
            out = self._win_audio("setcapture", capture_match)
            # ps1 "OK: <aniq nom>" qaytaradi — Meet/Zoom'da AYNAN shu mikrofon
            # tanlanishi kerak. Foydalanuvchiga ko'rsatamiz: Meet "Default"
            # emas, o'zi tanlagan mikrofonda tursa, suhbatdosh tarjimani
            # emas, xom ovozni eshitadi (jonli nosozlik, Windows 11).
            self.win_meeting_mic = (
                out.split("OK:", 1)[1].strip() if out.startswith("OK:") else ""
            )

    def _apply_routing_worker(self, render_match: str, capture_match: str) -> None:
        """Kabellarga o'tkazish — FON oqimida (GUI kutmaydi)."""
        with self._routing_lock:
            out = self._win_audio("startduplex", f"{render_match}|{capture_match}")
        values: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        # Zoom ochiqmi — SHU YERDA (fon oqimida) tekshiramiz: `tasklist`
        # ~200-300 ms oladi va GUI oqimida chaqirilsa Start bosilganda ilova
        # qotardi. Natija signaldan OLDIN yoziladi, demak `_routing_applied`
        # uni tayyor holda o'qiydi.
        self.zoom_running = self._meeting_app_running()
        self.device_scan_signals.routing.emit(
            values.get("render", ""), values.get("capture", ""), values.get("physical", "")
        )

    @staticmethod
    def _meeting_app_running() -> bool:
        """Zoom ish stoli ilovasi ochiqmi (fon oqimida chaqiriladi).

        Nima uchun kerak: Zoom mikrofonni MEETINGGA KIRGAN PAYTDA
        "yopishtirib" oladi va keyin Windows default'i o'zgarsa ergashmaydi.
        Brauzerdagi Meet esa ergashadi — 2026-07-28 da Meet ishlab, Zoom
        ishlamaganining sababi AYNAN SHU. Ilova Zoom'ni majburlay olmaydi,
        lekin ochiq ekanini bilsa foydalanuvchini ogohlantira oladi."""
        if platform.system() != "Windows":
            return False
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Zoom.exe", "/NH"],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=8, creationflags=creationflags,
            )
        except Exception as error:
            print(f"[ZOOM] tekshirib bo'lmadi: {error}", flush=True)
            return False
        running = "zoom.exe" in (result.stdout or "").lower()
        print(f"[ZOOM] ish stoli ilovasi ochiq: {running}", flush=True)
        return running

    def _routing_applied(self, speaker: str, mic: str, physical: str) -> None:
        """Fon oqimidagi routing tugadi — Meet qurilmalari nomini ko'rsatamiz."""
        self.win_meeting_speaker = speaker
        self.win_meeting_mic = mic
        self.win_physical_output = physical
        self._show_meet_devices_hint()
        self._warn_zoom_microphone(mic)

    def _warn_zoom_microphone(self, mic: str) -> None:
        """Zoom ochiq bo'lsa — mikrofonni qo'lda tanlashni ESLATAMIZ.

        Doimiy ko'k banner QAYTARILMADI (foydalanuvchi uni ataylab olib
        tashlatgan edi). Buning o'rniga FAQAT Zoom ochiq bo'lganda bir
        martalik bildirishnoma chiqadi — u oyna kichraytirilgan bo'lsa ham
        ko'rinadi, chunki foydalanuvchi ko'pincha tray'dan boshqaradi.

        Nima uchun majburiy: 2026-07-28 Zoom ko'rsatuvida ilova rus
        tarjimasini kabelga TO'G'RI yozgan (log bilan tasdiqlangan), lekin
        Zoom mikrofonni fizik mikrofonda ushlab turgani uchun hamkasblar
        xom o'zbekchani eshitgan."""
        if not getattr(self, "zoom_running", False) or not mic:
            return
        message = (
            f"Zoom ochiq. Zoom → Settings → Audio → Microphone = «{mic}» "
            "qilib qo'ying — aks holda suhbatdosh tarjimani emas, XOM "
            "ovozingizni eshitadi. Bir marta tanlansa Zoom eslab qoladi."
        )
        print(f"[ZOOM] ogohlantirish ko'rsatildi: mikrofon={mic!r}", flush=True)
        if self.tray is not None:
            pass  # bildirishnoma olib tashlandi (foydalanuvchi talabi)
        self.route_hint.setText(f"⚠️ {message}")
        self.route_hint.setVisible(True)

    def _show_meet_devices_hint(self) -> None:
        """Meet/Zoom qurilma nomlari — foydalanuvchi so'roviga ko'ra endi
        EKRANDA ko'rsatilmaydi (kerak emas). Nomlar logda qoladi, kerak
        bo'lsa tashxis uchun ishlatiladi."""
        self.meet_mic_hint.setVisible(False)

    def _win_restore_routing_async(self, blocking: bool = False) -> None:
        """Qurilmalarni FON oqimida tiklaydi (GUI qotmasin).

        `blocking=True` — ILOVA YOPILAYOTGANDA: tugashini KUTAMIZ. Aks holda
        fon oqimi `daemon` bo'lgani uchun `QApplication.quit()` uni darhol
        o'ldiradi va mikrofon/karnay KABELDA qolib ketadi (jonli nosozlik:
        "tray'dan chiqsam qaytmayapti, dasturdan chiqsam qaytadi" — chunki
        oynadan yopilganda ilova tray'da qolib, oqim tugashga ulgurardi).

        JONLI NOSOZLIK: "To'xtatishni bossam to'xtamayapti, UI qotib qolgan".
        Sabab — Stop bosilganda `_win_restore_routing()` GUI oqimida
        PowerShell ishga tushirardi (C# kompilyatsiya, ~2-3s), keyin jarayon
        tugaganda YANA bir marta. Endi ikkalasi ham fon oqimida."""
        if platform.system() != "Windows":
            return
        thread = threading.Thread(target=self._restore_routing_worker, daemon=True)
        thread.start()
        if blocking:
            thread.join(10.0)

    def _current_default_output(self) -> str:
        """Hozirgi ASOSIY chiqish qurilmasi nomi (tez, ctypes)."""
        try:
            from winaec import default_endpoint_name

            return default_endpoint_name(0)
        except Exception:
            return ""

    def _restore_routing_worker(self) -> None:
        """Qurilmalarni tiklaydi va NATIJANI TEKSHIRADI.

        DIQQAT (0.9.65-0.9.68 dagi JIDDIY XATO): bu yerda xato bilan
        `_win_restore_routing_async()` chaqirilgan edi — ya'ni fon oqimi
        O'ZINI qayta chaqirib, o'zi ushlab turgan `_routing_lock` ni kutib
        QOTIB qolardi. Natijada Stop bosilgach na mikrofon, na karnay
        tiklanmasdi va kompyuter ovozsiz qolardi.

        Endi haqiqiy tiklash chaqiriladi va natija TEKSHIRILADI: default
        chiqish hali ham virtual kabel bo'lsa, 3 martagacha qayta urinamiz."""
        with self._routing_lock:
            for attempt in range(1, 4):
                try:
                    self._win_restore_routing()
                except Exception as error:
                    print(f"[ROUTING] tiklashda xato: {error}", flush=True)
                current = self._current_default_output()
                if current and not is_virtual_device(current):
                    print(
                        f"[ROUTING] ovoz qaytdi: {current!r} (urinish {attempt})",
                        flush=True,
                    )
                    self.win_prev_render = ""
                    self.win_prev_capture = ""
                    return
                print(
                    f"[ROUTING] tiklanmadi (urinish {attempt}), hozir: {current!r}",
                    flush=True,
                )
                time.sleep(0.8)

    def _win_restore_routing(self) -> None:
        """Stop/chiqishda default qurilmalarni FIZIKga qaytaradi.

        Chiqish (karnay): naushnik ulangan bo'lsa o'shanga, aks holda fizik
        karnayga — foydalanuvchi talabi ("stop qilinganda realtek yoki
        naushnik ulangan bo'lsa o'shanga qaytsin"). Shu sabab Start'dan
        oldingi saqlangan default emas, HOZIRGI eng yaxshi fizik chiqish
        tanlanadi (sessiya davomida naushnik ulangan bo'lishi mumkin).
        Mikrofon esa Start'dan oldingi fizik default'ga (bo'lsa) qaytadi."""
        if platform.system() != "Windows":
            return
        prev_capture = getattr(self, "win_prev_capture", "")
        # Karnay/naushnik. TARTIB:
        #   1) Foydalanuvchi «ESHITAMAN» ro'yxatidan tanlagan qurilma — u
        #      AYNAN o'zi eshitadigan joy (bir nechta chiqishi bor
        #      kompyuterda avtomatik tanlov boshqa qurilmani olib, Stop'dan
        #      keyin ovoz umuman eshitilmay qolardi).
        #   2) Start'dan oldingi tizim default'i.
        #   3) Avtomatik: naushnik-afzal fizik chiqish.
        wanted_render = ""
        for candidate in (
            getattr(self, "preferred_output_name", ""),
            getattr(self, "win_prev_render", ""),
        ):
            if candidate and not is_virtual_device(candidate):
                wanted_render = candidate
                break
        wanted_capture = (
            prev_capture if prev_capture and not is_virtual_device(prev_capture) else ""
        )
        # BITTA chaqiruv (ps1 o'zi zaxira variantlarni ham bajaradi): Stop
        # bosilganda ilova qotib qolmasligi uchun.
        out = self._win_audio("stopduplex", f"{wanted_render}|{wanted_capture}")
        if "render=" not in out:
            # Zaxira: eski ko'p-chaqiruvli yo'l (eski ps1 bilan ishlaydi).
            if wanted_render and self._win_audio(
                "setrender", wanted_render
            ).startswith("OK"):
                pass
            else:
                self._win_audio("restorerender")
            if wanted_capture:
                self._win_audio("setcapture", wanted_capture)
            else:
                self._win_audio("restorecapture")
        # ESLATMA: `win_prev_*` BU YERDA TOZALANMAYDI — chaqiruvchi
        # (`_restore_routing_worker`) natijani tekshirib, kerak bo'lsa
        # QAYTA uriniши mumkin; tozalab yuborsak ikkinchi urinish
        # "qayerga qaytarish" ni bilmay qolardi.

    def restore_windows_default_speaker(self, match: str = "") -> str:
        """Default karnayni FIZIK qurilmaga qaytaradi (qo'lda tugma).

        "restorerender" "OK: <nom>" qaytaradi — chaqiruvchi ("KARNAY
        TIKLANDI") shu prefiksni kutadi. Ilgari "restore" ishlatilardi, u
        "render=..." qaytarardi va tugma ISHLASA HAM "topilmadi" deb
        yozardi (soxta ogohlantirish)."""
        return self._win_audio("restorerender")

    def _driver_installer_ready(self, path: str) -> None:
        self.driver_button.setEnabled(True)
        self.driver_button.setText("AUDIO DRIVER O‘RNATISH")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        # Windows: drayver o'zini default karnay qilib qo'ygan bo'lishi
        # mumkin — fizik karnayga qaytaramiz (ovoz eshitilmay qolmasin).
        if platform.system() == "Windows":
            self.restore_windows_default_speaker()
        if platform.system() == "Darwin":
            variant = "BlackHole 16ch" if "16ch" in Path(path).name else "BlackHole 2ch"
            instructions = (
                f"Rasmiy {variant} installer ochildi. Continue → Install ni bosing, "
                "administrator parolini kiriting va installer so‘raganda Mac’ni "
                "qayta ishga tushiring. Restart’dan keyin Live Translator BlackHole’ni "
                "avtomatik topadi."
            )
        elif getattr(self, "driver_variant", "") == "second":
            instructions = (
                "Ikkinchi audio kabel (Hi-Fi Cable) o‘rnatildi. IKKALASI rejimi "
                "ishlashi uchun Windows’ni bir marta qayta ishga tushiring — "
                "keyin hammasi avtomatik."
            )
        else:
            instructions = (
                "Virtual audio kabel o‘rnatildi. Endi tarjimani boshlashingiz "
                "mumkin. (Kabel ko‘rinmasa Windows’ni bir marta qayta ishga tushiring.)"
            )
        # Status "O'RNATILMOQDA…"da qotib qolmasin — o'rnatildi.
        self._set_status("AUDIO DRIVER O‘RNATILDI", "#22c55e")
        self._refresh_driver_state()
        QMessageBox.information(
            self,
            "Audio driver",
            instructions,
        )
        self._set_status("TAYYOR", "#94a3b8")

    def _driver_installer_failed(self, error: str) -> None:
        self.driver_button.setEnabled(True)
        self.driver_button.setText("QAYTA URINISH")
        QMessageBox.critical(self, "Driver o‘rnatilmadi", error)

    def _current_mode(self) -> str:
        # Ilova odatda FAQAT ikki tomonlama (foydalanuvchi talabi) — rejim
        # tanlash olib tashlandi. YAGONA istisno: «Meeting o'zbekcha»
        # belgisi. U yoqilganda ilova allaqachon mavjud va sinalgan
        # "gapirish" yo'lidan ketadi: kiruvchi kanal ochilmaydi, tizim
        # chiqishiga tegilmaydi (faqat mikrofon kabelga o'tkaziladi).
        return "outgoing" if getattr(self, "meeting_uz", False) else "duplex"

    def _current_pair(self) -> LanguagePair:
        mode = self._current_mode()
        return self.mode_pairs["incoming" if mode == "duplex" else mode]

    @staticmethod
    def _set_combo_code(combo: QComboBox, code: str) -> None:
        index = combo.findData(code)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _reset_captions(self) -> None:
        pair = self._current_pair()
        self.source_caption = ""
        self.target_caption = ""
        for captions in self.channel_captions.values():
            captions["source"] = ""
            captions["target"] = ""
        self.duplex_outgoing_original_text.setText("Siz: gap kutilmoqda…")
        self.duplex_outgoing_target_text.setText("Tarjima: shu yerda chiqadi…")
        self.source_text.setText("Gap kutilmoqda…")
        self.target_text.setText("Tarjima shu yerda chiqadi…")

    def _sync_mode_ui(self, apply_devices: bool) -> None:
        mode = self._current_mode()
        pair = self._current_pair()
        self.language_change_in_progress = True
        self.source_language_select.blockSignals(True)
        self.target_language_select.blockSignals(True)
        self._set_combo_code(self.source_language_select, pair.source)
        self._set_combo_code(self.target_language_select, pair.target)
        outgoing_pair = self.mode_pairs["outgoing"]
        self.duplex_outgoing_source.blockSignals(True)
        self.duplex_outgoing_target.blockSignals(True)
        self._set_combo_code(self.duplex_outgoing_source, outgoing_pair.source)
        self._set_combo_code(self.duplex_outgoing_target, outgoing_pair.target)
        self.duplex_outgoing_source.blockSignals(False)
        self.duplex_outgoing_target.blockSignals(False)
        self.source_language_select.blockSignals(False)
        self.target_language_select.blockSignals(False)
        self.language_change_in_progress = False
        self.swap_languages_button.setEnabled(pair.source != "auto")
        duplex = mode == "duplex"
        self.duplex_outgoing_language_panel.setVisible(duplex)
        self.duplex_outgoing_audio_panel.setVisible(False)
        self.duplex_outgoing_caption_panel.setVisible(True)
        self.language_label.setText("Tillar")
        # 530 → 596: «Meeting o'zbekcha» belgisi va navbat ko'rsatkichi.
        self.setFixedSize(385, 392)
        self._sync_meeting_uz_widgets()
        self._reset_captions()
        if apply_devices:
            self._apply_direction_devices(mode)
            self._refresh_driver_state()

    def _refresh_direction_labels(self) -> None:
        for index, definition in enumerate(PRODUCT_MODES):
            self.direction.setItemLanguages(
                index, pair_label(self.mode_pairs[definition.code])
            )
        self.direction.setItemLanguages(
            2, duplex_label(self.mode_pairs["incoming"], self.mode_pairs["outgoing"])
        )

    def _store_current_pair(self, pair: LanguagePair) -> None:
        mode = self._current_mode()
        if mode == "duplex":
            mode = "incoming"
        self.mode_pairs[mode] = pair
        self.settings.setValue(f"translation/{mode}/source", pair.source)
        self.settings.setValue(f"translation/{mode}/target", pair.target)
        self.settings.sync()
        self._refresh_direction_labels()
        self._sync_mode_ui(apply_devices=False)
        self._sync_tray()

    def _store_duplex_outgoing_pair(self, pair: LanguagePair) -> None:
        self.mode_pairs["outgoing"] = pair
        self.settings.setValue("translation/outgoing/source", pair.source)
        self.settings.setValue("translation/outgoing/target", pair.target)
        self.settings.sync()
        self._refresh_direction_labels()
        self._sync_mode_ui(apply_devices=False)

    def _init_simple_languages(self) -> None:
        """Sodda til panelini saqlangan qiymatlardan to'ldiradi (signalsiz)."""
        your = self.mode_pairs["outgoing"].source
        if your not in TARGET_CODES:
            your = "uz"
        meeting = self.mode_pairs["outgoing"].target
        if meeting not in TARGET_CODES or meeting == your:
            meeting = "en" if your != "en" else "ru"
        for combo, code in (
            (self.your_language_select, your),
            (self.meeting_language_select, meeting),
        ):
            combo.blockSignals(True)
            self._set_combo_code(combo, code)
            combo.blockSignals(False)
        self._apply_simple_languages(persist=False)

    def _apply_simple_languages(self, _index: int = -1, persist: bool = True) -> None:
        """Sodda paneldan ikki tomonlama til juftlarini yasaydi.

        "Mening tilim" = siz eshitasiz VA gapirasiz. "Tarjima tili" = siz
        gapirganda boshqa odamga shu tilda boradi. Eshitishda manba = AUTO.
        """
        your = str(self.your_language_select.currentData() or "uz")
        meeting = str(self.meeting_language_select.currentData() or "en")
        if meeting == your:
            meeting = next(c for c in ("en", "ru", "es", "uz") if c != your)
            self.meeting_language_select.blockSignals(True)
            self._set_combo_code(self.meeting_language_select, meeting)
            self.meeting_language_select.blockSignals(False)
        self.mode_pairs["incoming"] = LanguagePair("auto", your)
        self.mode_pairs["outgoing"] = LanguagePair(your, meeting)
        # Yashirin eski combolarni ham moslaymiz (ichki logika ular orqali
        # ishlashi mumkin) — signal bloklab.
        for combo, code in (
            (self.source_language_select, "auto"),
            (self.target_language_select, your),
            (self.duplex_outgoing_source, your),
            (self.duplex_outgoing_target, meeting),
        ):
            combo.blockSignals(True)
            self._set_combo_code(combo, code)
            combo.blockSignals(False)
        if persist:
            self.settings.setValue("translation/incoming/source", "auto")
            self.settings.setValue("translation/incoming/target", your)
            self.settings.setValue("translation/outgoing/source", your)
            self.settings.setValue("translation/outgoing/target", meeting)
            self.settings.sync()
        if getattr(self, "tray", None) is not None:
            self._sync_tray()

    def _source_language_changed(self, _index: int) -> None:
        if self.language_change_in_progress:
            return
        source = str(self.source_language_select.currentData() or "")
        self._store_current_pair(change_source(self._current_pair(), source))

    def _target_language_changed(self, _index: int) -> None:
        if self.language_change_in_progress:
            return
        target = str(self.target_language_select.currentData() or "")
        self._store_current_pair(change_target(self._current_pair(), target))

    def _duplex_outgoing_source_changed(self, _index: int) -> None:
        if self.language_change_in_progress:
            return
        source = str(self.duplex_outgoing_source.currentData() or "")
        self._store_duplex_outgoing_pair(
            change_source(self.mode_pairs["outgoing"], source)
        )

    def _duplex_outgoing_target_changed(self, _index: int) -> None:
        if self.language_change_in_progress:
            return
        target = str(self.duplex_outgoing_target.currentData() or "")
        self._store_duplex_outgoing_pair(
            change_target(self.mode_pairs["outgoing"], target)
        )

    def _swap_languages(self) -> None:
        try:
            pair = swap_pair(self._current_pair())
        except ValueError:
            return
        self._store_current_pair(pair)

    @staticmethod
    def _engine_log_path() -> Path:
        if platform.system() == "Darwin":
            return Path.home() / "Library" / "Logs" / APP_NAME / "engine.log"
        return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / APP_NAME / "engine.log"

    def _collect_logs_text(self) -> str:
        """Yuborish uchun loglarni bitta matnga yig'adi (oxirgi qismlari)."""
        parts: list[str] = [
            f"=== {APP_NAME} {APP_VERSION} ({APP_EDITION}) ===",
            f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
            f"Vaqt: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Oxirgi xato: {self.last_engine_error or '-'}",
            "",
        ]
        engine = self.engine_log_path
        for path, title, limit in (
            (engine.with_name("app.log"), "APP.LOG", 40_000),
            (engine, "ENGINE.LOG", 120_000),
            (engine.with_suffix(".prev.log"), "ENGINE.PREV.LOG", 40_000),
        ):
            try:
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                parts.append(f"----- {title} ({path.name}) -----")
                parts.append(text[-limit:])
                parts.append("")
            except OSError:
                continue
        return "\n".join(parts)

    def send_logs_to_support(self) -> None:
        """Loglarni yordam serveriga yuboradi (bitta tugma).

        Sabab: ilova turli kompyuterlarga o'rnatiladi (boshliq, hamkasblar),
        ularda SSH yo'q va log faylni har safar qo'lda olib kelish noqulay
        edi. Endi foydalanuvchi bitta tugma bosadi."""
        if not SUPPORT_URL or not SUPPORT_UPLOAD_TOKEN:
            self._set_status("YUBORISH SOZLANMAGAN", "#ef4444")
            return
        self._set_status("LOG YUBORILMOQDA…", "#f59e0b")
        threading.Thread(target=self._send_logs_worker, daemon=True).start()

    def _send_logs_worker(self) -> None:
        try:
            body = self._collect_logs_text().encode("utf-8", "replace")
            device = f"{platform.node()}"
            boundary = "----LiveTranslator" + uuid.uuid4().hex
            def part(name: str, value: str) -> bytes:
                return (
                    f"--{boundary}\r\nContent-Disposition: form-data; "
                    f'name="{name}"\r\n\r\n{value}\r\n'
                ).encode("utf-8")

            payload = b"".join(
                [
                    part("version", APP_VERSION),
                    part("device", device),
                    part("note", (self.last_engine_error or "")[:200]),
                    (
                        f"--{boundary}\r\nContent-Disposition: form-data; "
                        'name="file"; filename="logs.log"\r\n'
                        "Content-Type: text/plain\r\n\r\n"
                    ).encode("utf-8"),
                    body,
                    f"\r\n--{boundary}--\r\n".encode("utf-8"),
                ]
            )
            request = urllib.request.Request(
                f"{SUPPORT_URL.rstrip('/')}/logs",
                data=payload,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "x-lt-token": SUPPORT_UPLOAD_TOKEN,
                },
            )
            with urllib.request.urlopen(
                request, timeout=30, context=secure_ssl_context()
            ) as response:
                ok = response.status == 200
            self.support_signals.finished.emit(
                "LOG YUBORILDI ✓" if ok else "YUBORILMADI"
            )
        except Exception as error:
            print(f"[SUPPORT] log yuborilmadi: {error}", flush=True)
            self.support_signals.finished.emit(f"YUBORILMADI: {str(error)[:60]}")

    def _support_finished(self, message: str) -> None:
        colour = "#22c55e" if "✓" in message else "#ef4444"
        self._set_status(message, colour)
        QTimer.singleShot(6000, self._sync_status_after_send)

    def _sync_status_after_send(self) -> None:
        if self.process is None:
            self._set_status("TAYYOR", "#94a3b8")

    def _check_for_update(self) -> None:
        """Yangi versiya bor-yo'qligini serverdan so'raydi (fon rejimida)."""
        if not SUPPORT_URL:
            return
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self) -> None:
        try:
            suffix = f"?channel={APP_CHANNEL}" if APP_CHANNEL else ""
            request = urllib.request.Request(f"{SUPPORT_URL.rstrip('/')}/update{suffix}")
            with urllib.request.urlopen(
                request, timeout=15, context=secure_ssl_context()
            ) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            latest = str(data.get("version", "")).strip()
            url = str(data.get("url", "")).strip()
            if not latest or not url:
                return
            if self._version_tuple(latest) <= self._version_tuple(APP_VERSION):
                return
            self.update_signals.available.emit(latest, url)
        except Exception as error:
            print(f"[UPDATE] tekshirib bo'lmadi: {error}", flush=True)

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        parts = []
        for chunk in re.split(r"[.\-+]", value.strip()):
            parts.append(int(chunk) if chunk.isdigit() else 0)
        return tuple(parts[:4] or [0])

    def _update_available(self, version: str, url: str) -> None:
        self.update_url = url
        self.update_hint.setVisible(True)
        if self.process is None:
            # AVTOMATIK: tarjima ishlamayotgan bo'lsa foydalanuvchi hech
            # narsa bosmaydi — ilova o'zi yuklab, o'rnatuvchini ochadi
            # (foydalanuvchi talabi: "update kelsa avtomatik update qilsin").
            self.update_hint.setText(
                f"⬆️ Yangi versiya {version} — avtomatik yuklanmoqda…"
            )
            self.update_button.setVisible(False)
            self._install_update()
            return
        # Tarjima KETAYOTGAN bo'lsa uzmaymiz: tugma bilan o'zi tanlaydi.
        self.update_hint.setText(
            f"⬆️ Yangi versiya {version} tayyor. Tarjima tugagach o‘rnating."
        )
        self.update_button.setText(f"⬆️ {version} ni o‘rnatish")
        self.update_button.setVisible(True)

    def _install_update(self) -> None:
        """Yangi o'rnatuvchini yuklab, ishga tushiradi (ilova yopiladi)."""
        url = getattr(self, "update_url", "")
        if not url:
            return
        self.update_button.setEnabled(False)
        self.update_button.setText("YUKLANMOQDA…")
        threading.Thread(target=self._install_update_worker, args=(url,), daemon=True).start()

    def _install_update_worker(self, url: str) -> None:
        try:
            target = Path(tempfile.gettempdir()) / url.rsplit("/", 1)[-1]
            with urllib.request.urlopen(
                url, timeout=300, context=secure_ssl_context()
            ) as response, target.open("wb") as out:
                shutil.copyfileobj(response, out)
            self.update_signals.downloaded.emit(str(target))
        except Exception as error:
            self.update_signals.downloaded.emit(f"XATO: {error}")

    def _update_downloaded(self, path: str) -> None:
        if path.startswith("XATO:"):
            self.update_button.setEnabled(True)
            self.update_button.setText("⬆️ Qayta urinish")
            self.update_hint.setText(f"Yuklab bo‘lmadi: {path[5:][:120]}")
            return
        # ==================================================================
        # NEGA YANGILANISH ISHLAMAYOTGAN EDI ("update qayta-qayta qilsam ham
        # bo'lmayapti") — ildiz sabab:
        #
        # O'rnatuvchini shu yerdan to'g'ridan-to'g'ri ochsak, u ilovaning
        # BOLA jarayoni bo'lib qolardi. O'rnatuvchi ichida esa
        # `taskkill /IM "Live Translator.exe" /T /F` bor — `/T` bayrog'i
        # jarayonni BUTUN DARAXTI bilan o'ldiradi, ya'ni o'rnatuvchi
        # o'z-o'zini o'ldirardi. Natijada eski versiya joyida qolardi va
        # foydalanuvchi necha marta bosmasin, hech narsa o'zgarmasdi.
        #
        # TUZATISH: o'rnatuvchini ilova daraxtidan AJRATIB ishga tushiramiz.
        #   1) Vaqtinchalik .cmd yozamiz;
        #   2) uni alohida (DETACHED) ochamiz — u 3 soniya kutadi;
        #   3) ilova shu orada O'ZI toza yopiladi (audio yo'llari tiklanadi,
        #      fayllar qulfdan bo'shaydi);
        #   4) keyin o'rnatuvchi jim rejimda ishlaydi va yakunida dasturni
        #      qayta ochadi ([Run] dagi `skipifsilent` olib tashlangan).
        # ==================================================================
        try:
            if platform.system() == "Windows":
                script = Path(tempfile.gettempdir()) / "lt-update.cmd"
                script.write_text(
                    "@echo off\r\n"
                    "ping -n 4 127.0.0.1 >nul\r\n"
                    f'start "" "{path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n',
                    encoding="utf-8",
                )
                detached = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
                subprocess.Popen(
                    ["cmd", "/c", str(script)],
                    creationflags=detached,
                    close_fds=True,
                )
            else:
                subprocess.Popen(["open", path])
        except Exception as error:
            self.update_hint.setText(f"O‘rnatuvchi ochilmadi: {error}")
            return
        self.update_hint.setText("Yangilanmoqda — dastur o‘zi qayta ochiladi…")
        print("[UPDATE] o'rnatuvchi ajratilgan holda ishga tushdi, ilova yopilmoqda", flush=True)
        if platform.system() == "Windows":
            # O'rnatuvchi fayllarni qulfsiz topishi uchun o'zimiz chiqamiz.
            QTimer.singleShot(400, self._quit_from_tray)

    def _direction_changed(self, _index: int = -1) -> None:
        self.settings.setValue("translation/active_mode", self._current_mode())
        self.settings.sync()
        self._sync_mode_ui(apply_devices=True)
        self._sync_tray()

    def _set_controls(self, running: bool) -> None:
        devices_ready = (
            self.input_device.currentData() is not None
            and self.output_device.currentData() is not None
        )
        if self._current_mode() == "duplex":
            devices_ready = devices_ready and (
                self.duplex_outgoing_input.currentData() is not None
                and self.duplex_outgoing_output.currentData() is not None
            )
            if devices_ready:
                try:
                    validate_duplex_routes(self._duplex_routes())
                except (TypeError, ValueError):
                    devices_ready = False
        # DIQQAT: API kalit YO'Q bo'lsa ham tugma BOSILADIGAN qoladi —
        # bosilganda Sozlamalar ochiladi. Ilgari tugma o'chiq bo'lardi va
        # foydalanuvchi SABABINI BILMASDI (jonli nosozlik: yangi kompyuterda
        # "tarjimani boshlash umuman bosilmayapti", log: kalit=False).
        ready = bool(devices_ready and not self.license_check_in_progress)
        if not self.api_key and not running:
            if getattr(self, "_api_hint_shown", None) is not True:
                self._api_hint_shown = True
                self.route_hint.setText(
                    "🔑 API kalit kiritilmagan — «⚙️» (Sozlamalar) tugmasini "
                    "bosib kalitni kiriting, keyin «Tarjimani boshlash»."
                )
                self.route_hint.setVisible(True)
                self._set_status("API KALIT KERAK", "#f59e0b")
        elif self.api_key:
            self._api_hint_shown = False
        if not ready and not running and getattr(self, "_last_ready_state", None) != ready:
            print(
                f"[UI] Start tugmasi O'CHIQ: kalit={bool(self.api_key)} "
                f"qurilmalar={devices_ready} litsenziya={self.license_check_in_progress}",
                flush=True,
            )
            # QAYSI qurilma yetishmayotganini aniq ko'rsatamiz — "qurilmalar=False"
            # ning o'zi sababni aytmaydi (jonli nosozlik: yangi kompyuterda
            # tugmalar umuman bosilmadi, sabab logda ko'rinmadi).
            try:
                print(
                    "[UI]   kirish="
                    f"{self._device_name(self.input_device)!r} "
                    f"chiqish={self._device_name(self.output_device)!r} "
                    f"mikrofon={self._device_name(self.duplex_outgoing_input)!r} "
                    f"kabel={self._device_name(self.duplex_outgoing_output)!r}",
                    flush=True,
                )
                if self._current_mode() == "duplex":
                    try:
                        validate_duplex_routes(self._duplex_routes())
                        print("[UI]   duplex tekshiruvi: OK", flush=True)
                    except (TypeError, ValueError) as error:
                        print(f"[UI]   duplex tekshiruvi XATO: {error}", flush=True)
            except Exception as error:
                print(f"[UI]   holatni o'qib bo'lmadi: {error}", flush=True)
        self._last_ready_state = ready
        self.start_button.setEnabled(not running and ready)
        self.stop_button.setEnabled(running)
        # Tarjima ketayotganda til tanlash QOTADI (foydalanuvchi talabi):
        # rejim o'rtada almashsa Gemini sessiyasi buziladi.
        with suppress(Exception):
            self.your_language_select.setEnabled(not running)
            self.meeting_language_select.setEnabled(not running)
            self.wave.set_running(running)
        # Plitkalar haqiqiy holatni ko'rsatsin (Start bosilgach rangli bo'ladi).
        self._sync_tiles()
        # Tray ikoni: tarjima ketayotganda YASHIL, aks holda odatdagidek.
        with suppress(Exception):
            if self.tray is not None:
                self.tray.setIcon(self._tray_pixmap(running=running))
        self.direction.setEnabled(not running)
        self.source_language_select.setEnabled(not running)
        self.target_language_select.setEnabled(not running)
        self.swap_languages_button.setEnabled(
            not running and self._current_pair().source != "auto"
        )
        self.input_device.setEnabled(not running)
        self.output_device.setEnabled(not running)
        self.duplex_outgoing_source.setEnabled(not running)
        self.duplex_outgoing_target.setEnabled(not running)
        self.duplex_outgoing_input.setEnabled(not running)
        self.duplex_outgoing_output.setEnabled(not running)
        self.start_button.setStyleSheet(
            (
                "QPushButton { background: #15845a; color: white; font-size: 13px; } "
                "QPushButton:hover { background: #1a9d6b; } "
                "QPushButton:pressed { background: #0f6b48; }"
            )
            if not running and ready
            else "QPushButton { background: #263449; color: #77879d; font-size: 13px; }"
        )
        self.stop_button.setStyleSheet(
            (
                "QPushButton { background: #dc3f4f; color: white; font-size: 13px; } "
                "QPushButton:hover { background: #e7505f; } "
                "QPushButton:pressed { background: #b33140; }"
            )
            if running
            else "QPushButton { background: #263449; color: #77879d; font-size: 13px; }"
        )
        self._sync_tray(running=running, ready=ready)

    def _set_status(self, text: str, color: str) -> None:
        shown = t(text)  # lug'atda bo'lsa joriy tilga o'giradi
        self.status.setText(f"●  {shown}")
        self.status.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700;")
        status_action = getattr(self, "tray_status_action", None)
        if status_action is not None:
            status_action.setText(shown.capitalize())
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.setToolTip(f"{APP_NAME} — {shown.capitalize()}")

    def start_translator(self) -> None:
        # DIAGNOSTIKA: "Start bosilmayapti" shikoyatida log jim edi — endi
        # har bosish va har to'siq yoziladi (masofadan tashxis uchun).
        print("[UI] Start bosildi", flush=True)
        if self.process is not None or self.license_check_in_progress:
            print(
                f"[UI] Start rad: process={self.process is not None} "
                f"litsenziya_tekshiruvi={self.license_check_in_progress}",
                flush=True,
            )
            return
        if not self.api_key:
            print("[UI] Start rad: API kalit yo'q", flush=True)
            self.edit_settings(required=True)
            return
        if self.input_device.currentData() is None or self.output_device.currentData() is None:
            # O'ZINI TUZATADI: ro'yxat vaqtincha bo'sh bo'lishi mumkin (fon
            # skaneri PortAudio'ni yangilayotgan payt). Oyna yashirin bo'lganda
            # tray'dan Start bosilsa aynan shu holat "hech narsa ishlamayapti"
            # bo'lib ko'rinardi — endi ro'yxatni yangilab, qayta urinamiz.
            print("[UI] Qurilmalar bo'sh — ro'yxat yangilanmoqda…", flush=True)
            self._refresh_audio_devices()
            self._apply_direction_devices(self._current_mode())
        if self.input_device.currentData() is None or self.output_device.currentData() is None:
            print(
                "[UI] Start rad: qurilma tanlanmagan "
                f"(kirish={self.input_device.currentData()}, "
                f"chiqish={self.output_device.currentData()})",
                flush=True,
            )
            self._set_status("AUDIO QURILMA TANLANG", "#ef4444")
            return
        if self._current_mode() == "duplex":
            try:
                validate_duplex_routes(self._duplex_routes())
            except (TypeError, ValueError) as error:
                print(f"[UI] Start rad: duplex yo'llari xato: {error}", flush=True)
                self._set_status("IKKINCHI AUDIO YO‘LI KERAK", "#ef4444")
                self.route_hint.setText(str(error))
                return
        try:
            client = LicenseClient(
                self.control_url,
                self.license_key,
                self.device_id,
                APP_VERSION,
            )
        except LicenseError as error:
            self._license_failed(str(error))
            return
        self.license_client = client
        if not client.enabled:
            self._launch_translator()
            return
        if not self.license_key:
            self._set_status("LITSENZIYA KERAK", "#ef4444")
            self.edit_settings()
            return
        self.license_check_in_progress = True
        self._set_status("RUXSAT TEKSHIRILMOQDA…", "#f59e0b")
        self._set_controls(running=False)
        threading.Thread(target=self._activate_license, daemon=True).start()

    def _activate_license(self) -> None:
        try:
            assert self.license_client is not None
            name = self.license_client.activate()
            self.license_signals.activated.emit(name)
        except Exception as error:
            self.license_signals.failed.emit(str(error))

    def _license_activated(self, _name: str) -> None:
        self.license_check_in_progress = False
        self.heartbeat_failures = 0
        self._launch_translator()

    def _license_failed(self, error: str) -> None:
        self.license_check_in_progress = False
        self.last_engine_error = error
        self._set_status("LITSENZIYA RAD ETILDI", "#ef4444")
        self.source_language.setText("RUXSAT HOLATI")
        self.source_text.setText(error[:180])
        self._set_controls(running=False)

    def _launch_translator(self) -> None:
        mode = self._current_mode()
        # Kiruvchi kanal shu ishga tushirishda ochiladimi. Jonli almashtirish
        # faqat ochilgan kanalni to'xtata oladi — yo'q kanalni yo'ldan
        # qo'shib bo'lmaydi (u holda Stop→Start kerak).
        self.engine_has_incoming = mode == "duplex"
        # Har ishga tushirishda eski buyruqlar tozalanadi (aks holda o'tgan
        # sessiyadagi `incoming_paused` yangi sessiyani darhol to'xtatardi).
        self.device_state_path.unlink(missing_ok=True)
        process_arguments = ["--voice", "Charon"]
        if getattr(self, "quality_mode", False):
            # Gapni kutib, to'liq gapni tarjima qilish rejimi.
            process_arguments.append("--quality")
        control_sessions: list[tuple[str, str, str, str, str]] = []
        try:
            if mode == "duplex":
                routes = self._duplex_routes()
                validate_duplex_routes(routes)
                incoming_pair = self.mode_pairs["incoming"]
                outgoing_pair = self.mode_pairs["outgoing"]
                # ROUTING AVVAL: win_prev_render (routing'dan oldingi haqiqiy
                # karnay) shu yerda saqlanadi va incoming tarjima chiqishiga
                # kerak bo'ladi.
                incoming_output_arg = str(routes.incoming_output.index)
                if platform.system() == "Darwin":
                    self.previous_system_output = route_output_to(
                        routes.incoming_input.name
                    )
                    self.previous_system_input = route_input_to(
                        routes.outgoing_output.name
                    )
                elif platform.system() == "Windows":
                    # Zoom speaker -> kiruvchi kabel; Zoom mic -> chiquvchi kabel.
                    # Kiruvchi kabel nomi SAQLANADI: «Meeting o'zbekcha» ni
                    # jonli o'chirib-yoqqanda chiqishni shu kabelga qaytarish
                    # kerak bo'ladi (qayta hisoblash uchun qurilma ro'yxatini
                    # o'qish kerak edi — meeting o'rtasida qimmat).
                    self.win_incoming_cable_match = self._win_cable_match(
                        routes.incoming_input.name
                    )
                    self._win_apply_routing(
                        self.win_incoming_cable_match,
                        self._win_cable_match(routes.outgoing_output.name),
                    )
                    # Incoming tarjima (UZ) foydalanuvchi AYNAN ESHITAYOTGAN
                    # qurilmaga boradi = routing'dan oldingi tizim DEFAULT'i
                    # (win_prev_render). Naushnik/monitor (masalan "P2961")
                    # default bo'lsa — o'shanga; karnay default bo'lsa — karnayga.
                    # findoutput faqat ZAXIRA: default virtual kabel bo'lib
                    # qolган bo'lsa (oldingi sessiya tiklamagan) ACTIVE
                    # naushnik/fizik qurilmani tanlaydi.
                    # 1) ULANGAN naushnik bo'lsa — DOIM o'shanga (foydalanuvchi
                    #    naushnikni tizim default qilmagan bo'lsa ham: Bluetooth
                    #    naushnik ulanganda tizim default'i ko'pincha karnayda
                    #    qolib, tarjima quloqqa bormasdi).
                    # 2) Aks holda foydalanuvchi eshitayotgan qurilma
                    #    (win_prev_render) — routing'dan oldingi tizim default'i.
                    # 3) Zaxira: findoutput (default kabel bo'lib qolgan hol).
                    headphone = self._connected_headphone_name()
                    prev = getattr(self, "win_prev_render", "")
                    chosen_by_user = getattr(self, "preferred_output_name", "")
                    if chosen_by_user and not is_virtual_device(chosen_by_user):
                        # Foydalanuvchi «ESHITAMAN» ro'yxatidan o'zi tanlagan —
                        # avtomatik tanlovdan USTUN. Kompyuterda bir nechta
                        # chiqish bo'lganda (Realtek karnay + monitor audio)
                        # tizim default'i noto'g'ri bo'lishi mumkin edi.
                        incoming_output_arg = chosen_by_user
                        print(
                            f"[ROUTING] foydalanuvchi tanlagan chiqish: {chosen_by_user!r}",
                            flush=True,
                        )
                    elif headphone:
                        incoming_output_arg = headphone
                        print(f"[ROUTING] naushnik tanlandi: {headphone!r}", flush=True)
                    elif prev and not is_virtual_device(prev):
                        incoming_output_arg = prev
                    else:
                        # startduplex allaqachon "physical=" qaytargan —
                        # QO'SHIMCHA PowerShell chaqiruvi kerak emas (tezlik).
                        chosen = getattr(self, "win_physical_output", "")
                        if not chosen:
                            with suppress(Exception):
                                chosen = self._win_audio("findoutput").strip()
                        if chosen and not is_virtual_device(chosen):
                            incoming_output_arg = chosen
                # FEEDBACK-GATE O'CHIRILDI (v0.9.33). Sabab: suhbatdosh
                # gapirganda incoming tarjima karnayda deyarli UZLUKSIZ ijro
                # etiladi (kechikish + silliqlash tufayli), gate esa shu paytda
                # mikrofonni jim qiladi — natijada foydalanuvchi gapi HECH
                # QACHON o'tmasdi ("gapirish umuman ishlamayapti"). Endi
                # mikrofon doim ochiq: gapingiz doim tarjima bo'lib meetingga
                # boradi. NAUSHNIK ishlatilsa echo yo'q (tarjima quloqqa
                # chiqadi, mikrofon eshitmaydi); karnay bilan suhbatdosh o'z
                # ovozini takror eshitishi mumkin — UI shuni ogohlantiradi.
                incoming_out_name = (
                    incoming_output_arg
                    if not incoming_output_arg.isdigit()
                    else routes.incoming_output.name
                )
                if self._output_is_ear_safe(incoming_out_name):
                    # NAUSHNIK/garnitura: tarjima quloqqa chiqadi, mikrofon uni
                    # eshitmaydi -> feedback yo'q -> himoya KERAK EMAS -> to'liq
                    # ikki tomonlama (tugma bosmasdan, istalgan payt gapirish).
                    process_arguments.append("--no-gate")
                    self.route_hint.setVisible(False)
                elif platform.system() == "Windows":
                    # Karnay (Windows): Microsoft AEC (Voice Capture DSP) —
                    # exo tizim darajasida o'chiriladi, mikrofon DOIM ochiq,
                    # Ctrl bosish KERAK EMAS. AEC ishga tushmasa dvigatel
                    # O'ZI push-to-talk'ka qaytadi (log: "[AEC] ishlamadi").
                    # LT_SPEAKER_MODE=ptt bilan eski rejimga majburlash mumkin.
                    if os.environ.get("LT_SPEAKER_MODE", "").lower() == "ptt":
                        process_arguments.append("--push-to-talk")
                        self.route_hint.setText(
                            "🎤 Karnay rejimi: gapirish uchun CTRL (chap yoki o‘ng) "
                            "tugmasini bosib turing."
                        )
                    else:
                        process_arguments.append("--winaec")
                        self.route_hint.setText(
                            "🔊 Karnay: exo-bekor qilish (Windows AEC) yoqildi "
                            "— erkin gapiring, tugma bosish shart emas. Muammo "
                            "bo‘lsa ilova o‘zi Ctrl rejimiga qaytadi."
                        )
                    self.route_hint.setVisible(True)
                else:
                    # macOS karnay: feedback-gate (navbatlashib gapirish).
                    self.route_hint.setText(
                        "🎧 To‘liq ikki tomonlama uchun naushnik ulang. Karnay "
                        "bilan: suhbatdosh gapirmayotganda gapiring."
                    )
                    self.route_hint.setVisible(True)
                if self._is_handsfree_mic(routes.outgoing_input.name):
                    # Boshqa mikrofon topilmagan: BT garnitura HFP rejimiga
                    # o'tib ovoz sifati keskin pasayadi (va stereo chiqish
                    # uzilishi mumkin) — foydalanuvchi sababini bilsin.
                    self.route_hint.setText(
                        "⚠️ Mikrofon sifatida Bluetooth garnitura tanlandi — "
                        "ovoz sifati pasayadi. Iloji bo‘lsa webcam yoki USB "
                        "mikrofon ulab, dasturni qayta ishga tushiring."
                    )
                    self.route_hint.setVisible(True)
                # ENG KO'P UCHRAYDIGAN NOSOZLIK: Meet/Zoom o'zi tanlagan
                # mikrofonda tursa (tizim default'ida emas), suhbatdosh
                # TARJIMANI EMAS, xom ovozni eshitadi. Ilova tarjimani qaysi
                # kabelga yozayotgan bo'lsa, Meet AYNAN o'shaning "Output"
                # tomonini tanlashi kerak — nomini o'zimiz ko'rsatamiz.
                # Nomlar fon oqimidagi routing tugagach to'ladi — o'shanda
                # `_routing_applied` yana chaqiradi.
                self._show_meet_devices_hint()
                # Tarjima ovozi QAYSI qurilmadan chiqishini aniq aytamiz.
                # Jonli nosozlik: matn ko'rinadi, ovoz eshitilmaydi — chunki
                # ilova tizim default'iga (Realtek) chiqaradi, foydalanuvchi
                # esa monitor/eshitgichdan eshitadi. Ilova buni BILA OLMAYDI,
                # shuning uchun nomini ko'rsatib, almashtirishni eslatamiz.
                heard_on = incoming_out_name or "?"
                self.route_hint.setText(
                    f"🔈 Tarjima ovozi «{heard_on}» qurilmasidan chiqadi. "
                    "Eshitmasangiz — yuqoridagi ESHITAMAN ro'yxatidan boshqasini "
                    "tanlab «▶ Sinov» bosing."
                )
                self.route_hint.setVisible(True)
                process_arguments.extend(
                    [
                        "--duplex",
                        "--incoming-source-language",
                        incoming_pair.source,
                        "--incoming-target-language",
                        incoming_pair.target,
                        # Qurilmalarni NOM bilan uzatamiz (index EMAS): GUI
                        # va dvigatel qurilma index'lari mos kelmasligi mumkin
                        # (tinglashdagi kabi), shunda dvigatel noto'g'ri kabel/
                        # qurilma ochib, ikki tomonlama buzilardi.
                        "--incoming-input-device",
                        routes.incoming_input.name,
                        "--incoming-output-device",
                        incoming_output_arg,
                        "--outgoing-source-language",
                        outgoing_pair.source,
                        "--outgoing-target-language",
                        outgoing_pair.target,
                        "--outgoing-input-device",
                        routes.outgoing_input.name,
                        "--outgoing-output-device",
                        routes.outgoing_output.name,
                    ]
                )
                control_sessions = [
                    (
                        "incoming",
                        incoming_pair.source,
                        incoming_pair.target,
                        routes.incoming_input.name,
                        routes.incoming_output.name,
                    ),
                    (
                        "outgoing",
                        outgoing_pair.source,
                        outgoing_pair.target,
                        routes.outgoing_input.name,
                        routes.outgoing_output.name,
                    ),
                ]
            else:
                input_id = int(self.input_device.currentData())
                output_id = int(self.output_device.currentData())
                input_name = self._device_name(self.input_device)
                output_name = self._device_name(self.output_device)
                input_virtual = is_virtual_device(input_name)
                output_virtual = is_virtual_device(output_name)
                # Dvigatelga uzatiladigan chiqish argumenti. Odatda index,
                # lekin Windows tinglashda NOM bilan uzatamiz (pastga qarang)
                # — GUI va dvigatel qurilma index'lari mos kelmasligi mumkin.
                output_arg = str(output_id)
                if mode == "outgoing" and not output_virtual:
                    # XAVFSIZLIK TO'RI: Gapirish chiqishi fizik karnay bo'lib
                    # qolgan bo'lsa, tarjima Zoom o'rniga foydalanuvchining
                    # o'ziga chiqadi (real xato). Virtual kabelga majburan
                    # o'tkazamiz; kabel umuman bo'lmasagina quyida xato beriladi.
                    if self._select_device_kind(
                        self.output_device,
                        virtual=True,
                        preferred_words=(
                            "blackhole 2ch",
                            "cable input",
                            "hi-fi cable",
                            "vb-audio hi-fi",
                        ),
                    ):
                        output_id = int(self.output_device.currentData())
                        output_name = self._device_name(self.output_device)
                        output_virtual = is_virtual_device(output_name)
                if mode == "outgoing" and (input_virtual or not output_virtual):
                    raise ValueError(
                        "GAPIRISH rejimi uchun fizik mikrofon va virtual chiqish kerak."
                    )
                if is_forbidden_route(input_name, output_name, input_id, output_id):
                    raise ValueError(
                        "Bir virtual kabelni ham input, ham output qilish feedback loop yaratadi."
                    )
                if platform.system() == "Darwin" and input_virtual and not output_virtual:
                    self.previous_system_output = route_output_to(input_name)
                if platform.system() == "Darwin" and output_virtual and not input_virtual:
                    self.previous_system_input = route_input_to(output_name)
                if platform.system() == "Windows":
                    if input_virtual and not output_virtual:
                        # Tinglash: Zoom speaker -> kabel (app o'qiydigan kabel).
                        self._win_apply_routing(self._win_cable_match(input_name), "")
                        # Tarjimani ROUTING'DAN OLDINGI haqiqiy karnayga
                        # (win_prev_render) NOM bilan chiqaramiz. Default endi
                        # kabel bo'lgani uchun avto-tanlash/index noto'g'ri
                        # qurilma (bo'sh quloqchin uyasi yoki kabel) tanlab,
                        # foydalanuvchi tarjimani ESHITMASDI (real xato).
                        prev = getattr(self, "win_prev_render", "")
                        if prev and not is_virtual_device(prev):
                            output_name = prev
                            output_arg = prev
                    elif output_virtual and not input_virtual:
                        # Gapirish: Zoom mic -> kabel (app yozadigan kabel).
                        # DIQQAT: tizim CHIQISHIGA tegilmaydi (birinchi arg
                        # bo'sh) — meeting ovozi karnayda jonli qoladi.
                        self._win_apply_routing("", self._win_cable_match(output_name))
                        # AKS-SADO HIMOYASI. Bu rejimda meeting ovozi
                        # KARNAYDAN chiqadi, ya'ni fizik mikrofon uni
                        # eshitadi. Himoyasiz dastur BOSHQALARNING gapini
                        # "siz gapirdingiz" deb tarjima qilib meetingga
                        # qaytaradi — 2026-07-28 Zoom ko'rsatuvida aynan shu
                        # bo'lgan (log: [OUTGOING] «Alloh, how are you?» →
                        # RU, o'sha soniyada [INCOMING] «Hello, how are
                        # you?»). Naushnikda bu fizik jihatdan yo'q.
                        reference = self._aec_reference_output()
                        if reference and not self._output_is_ear_safe(reference):
                            process_arguments.extend(
                                ["--winaec", "--winaec-speaker", reference]
                            )
                            self.route_hint.setText(
                                f"🔊 Meeting ovozi «{reference}» dan jonli eshitiladi. "
                                "Exo-bekor qilish (AEC) yoqildi — erkin gapiring."
                            )
                            self.route_hint.setVisible(True)
                pair = self._current_pair()
                process_arguments.extend(
                    [
                        "--target-language",
                        pair.target,
                        "--source-language",
                        pair.source,
                        # Kirish ham NOM bilan (index EMAS). Gapirishda ilova
                        # default mikrofonni kabelga o'zgartiradi — bu qurilma
                        # index'larini suradi va index bilan uzatilgan kirish
                        # NOTO'G'RI qurilmani (kabelni) ochib, "input va output
                        # bir kabel" feedback xatosini berardi.
                        "--input-device",
                        input_name,
                        "--output-device",
                        output_arg,
                    ]
                )
                if mode == "outgoing" and self.monitor_enabled:
                    # Tarjima virtual kabelga ketadi — foydalanuvchi o'zi
                    # eshitishi uchun fizik chiqishga nusxa beramiz
                    # (mikrofon nazorat ijrosi paytida gate qilinadi).
                    monitor = self._physical_output_name()
                    if monitor:
                        process_arguments.extend(["--monitor-device", monitor])
                control_sessions = [
                    (mode, pair.source, pair.target, input_name, output_name)
                ]
        except (TypeError, ValueError, RuntimeError) as error:
            self._restore_system_audio()
            self._set_status("AUDIO YO‘NALTIRISH XATOSI", "#ef4444")
            self.source_language.setText("Suhbatdoshingiz gapiradi")
            self.source_text.setText(str(error)[:180])
            return
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("GOOGLE_API_KEY", self.api_key)
        environment.insert("PYTHONUNBUFFERED", "1")
        # Windows: bola jarayon quvurni lokal kod sahifasida ochmasin.
        environment.insert("PYTHONIOENCODING", "utf-8:replace")
        # Qurilma almashishi uchun kanal: GUI yozadi, dvigatel o'qiydi.
        self.device_state_path.unlink(missing_ok=True)
        environment.insert("LIVE_TRANSLATOR_DEVICE_STATE", str(self.device_state_path))
        self.engine_log_path.parent.mkdir(parents=True, exist_ok=True)
        # Oldingi sessiya logini saqlab qolamiz: muammo yuz bergach
        # foydalanuvchi ko'pincha ilovani qayta ishga tushiradi va
        # dalil yo'qolib ketardi.
        previous = self.engine_log_path.with_suffix(".prev.log")
        if self.engine_log_path.is_file():
            previous.unlink(missing_ok=True)
            try:
                self.engine_log_path.rename(previous)
            except OSError:
                self.engine_log_path.unlink(missing_ok=True)
        self.engine_log_position = 0
        environment.insert("LIVE_TRANSLATOR_ENGINE_LOG", str(self.engine_log_path))
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(PROJECT_DIR))
        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = ["--engine"]
        else:
            program = sys.executable
            arguments = [str(Path(__file__).resolve()), "--engine"]
        arguments.extend(process_arguments)
        # The PyInstaller windowed bootloader can swallow a QProcess pipe on
        # macOS. Drain it, while the mirrored local log carries UI events.
        process.readyReadStandardOutput.connect(self._drain_process_output)
        process.errorOccurred.connect(
            lambda _error: setattr(self, "process_error", process.errorString())
        )
        process.finished.connect(self._process_finished)
        process.start(program, arguments)
        if not process.waitForStarted(5000):
            self._set_status("ISHGA TUSHMADI", "#ef4444")
            self._restore_system_audio()
            process.deleteLater()
            return
        self.process = process
        self.stop_requested = False
        self.connected = False
        self.connected_channels.clear()
        self.output_buffer = ""
        self.last_engine_error = ""
        self.process_error = ""
        self._set_status("ULANMOQDA…", "#f59e0b")
        self._set_controls(running=True)
        self.engine_log_timer.start()
        if platform.system() != "Darwin":
            self.device_signature = self._output_device_signature()
            # O'CHIRILDI (0.9.67): bu taymer har 3 soniyada PortAudio'ni
            # to'liq qayta yuklardi (`sd._terminate()/_initialize()`), ya'ni
            # butun Windows audio quyi tizimini qo'zg'atardi. Natijada
            # YouTube/video ovozi uzilib, tarjima kechikardi, kompyuter
            # "qotgandek" bo'lardi. Chiqish qurilmasi endi foydalanuvchi
            # tanlovi bilan qat'iy belgilangani uchun (ESHITAMAN, saqlanadi)
            # bu kuzatuv kerak emas. Qurilma almashsa: Stop -> Start.
            # self.device_change_timer.start()
        self.connection_timer.start(20_000 if mode == "duplex" else 12_000)
        if self.license_client and self.license_client.enabled:
            threading.Thread(
                target=self._start_control_sessions,
                args=(control_sessions,),
                daemon=True,
            ).start()
            self.heartbeat_timer.start()

    def _start_control_sessions(
        self,
        sessions: list[tuple[str, str, str, str, str]],
    ) -> None:
        try:
            assert self.license_client is not None
            for mode, source, target, input_name, output_name in sessions:
                self.license_client.start_session(
                    target,
                    input_name,
                    output_name,
                    source_language=source,
                    mode=mode,
                )
        except Exception as error:
            self.license_signals.heartbeat_failed.emit(str(error))

    def _send_heartbeat(self) -> None:
        if (
            self.process is None
            or not self.license_client
            or not self.license_client.enabled
            or self.heartbeat_in_progress
        ):
            return
        self.heartbeat_in_progress = True
        threading.Thread(target=self._heartbeat_worker, daemon=True).start()

    def _heartbeat_worker(self) -> None:
        try:
            assert self.license_client is not None
            self.license_client.heartbeat()
            self.license_signals.heartbeat_ok.emit()
        except Exception as error:
            self.license_signals.heartbeat_failed.emit(str(error))

    def _heartbeat_ok(self) -> None:
        self.heartbeat_in_progress = False
        self.heartbeat_failures = 0

    def _heartbeat_failed(self, error: str) -> None:
        self.heartbeat_in_progress = False
        self.heartbeat_failures += 1
        revoked = any(
            word in error.casefold()
            for word in ("o‘chirilgan", "ochirilgan", "revoked", "muddati tugagan")
        )
        if not revoked and self.heartbeat_failures < 2:
            self._set_status("BOSHQARUV SERVERI KUTILMOQDA…", "#f59e0b")
            return
        self.last_engine_error = error
        self._set_status("LITSENZIYA TO‘XTATILDI", "#ef4444")
        if self.process:
            self.process.kill()

    def stop_translator(self) -> None:
        print("[UI] Stop bosildi", flush=True)
        if self.process is None:
            return
        self.stop_requested = True
        self._set_status("TO‘XTATILMOQDA…", "#f59e0b")
        # Ovoz qurilmalarini DARHOL fizikka qaytaramiz — jarayon tugashini
        # (~6s) kutmasdan, foydalanuvchi videoni darhol eshitsin. Jarayon
        # tugaganda ham yana bir bor tiklanadi (zararsiz).
        if platform.system() == "Windows":
            self._win_restore_routing_async()
        self.process.terminate()
        QTimer.singleShot(6000, self._force_stop)

    def _force_stop(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def _drain_process_output(self) -> None:
        if not self.process:
            return
        self.process.readAllStandardOutput()

    def _read_engine_log(self) -> None:
        try:
            size = self.engine_log_path.stat().st_size
            if size < self.engine_log_position:
                self.engine_log_position = 0
                self.output_buffer = ""
            with self.engine_log_path.open("r", encoding="utf-8", errors="replace") as log:
                log.seek(self.engine_log_position)
                chunk = log.read()
                self.engine_log_position = log.tell()
        except (FileNotFoundError, OSError):
            return
        if not chunk:
            return
        self.output_buffer += chunk
        while "\n" in self.output_buffer:
            line, self.output_buffer = self.output_buffer.split("\n", 1)
            self._handle_line(line.strip())

    _ENGINE_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}\s+")

    def _handle_line(self, line: str) -> None:
        # Dvigatel satrlari endi vaqt bilan boshlanadi ("12:34:56 [INCOMING] …").
        # Qolgan tahlil eski formatga tayanadi — vaqtni kesib tashlaymiz.
        line = self._ENGINE_TIMESTAMP.sub("", line, count=1)
        channel = ""
        if line.startswith("[") and "] " in line:
            candidate, remainder = line[1:].split("] ", 1)
            if candidate in {"INCOMING", "OUTGOING"}:
                channel = candidate
                line = remainder
        if "\u203a " in line or "› " in line:
            # Transkript satri keldi — jonli to'lqin puls oladi (arzon:
            # faqat bitta float o'zgaradi, chizish taymerda).
            with suppress(Exception):
                self.wave.pulse()
        if line.startswith("Xato:"):
            self.last_engine_error = line.removeprefix("Xato:").strip()
            return
        if is_engine_connected_line(line):
            if self._current_mode() == "duplex":
                if channel:
                    self.connected_channels.add(channel)
                if self.connected_channels != {"INCOMING", "OUTGOING"}:
                    self._set_status(
                        f"ULANMOQDA… {len(self.connected_channels)}/2", "#f59e0b"
                    )
                    return
                self._set_status("IKKALA TARJIMA ISHLAYAPTI", "#22c55e")
            else:
                self._set_status("TARJIMA ISHLAYAPTI", "#22c55e")
            self.connected = True
            self.connection_timer.stop()
            return
        if "[AEC] ishlamadi" in line:
            # Exo-bekor qilish ishga tushmadi -> dvigatel push-to-talk'ga
            # o'tdi. Ilgari ekranda "erkin gapiring" deb turaverardi va
            # foydalanuvchi Ctrl bosmasdan gapirib, hech kim eshitmasdi
            # (jonli nosozlik: "boshqa kompyuterda eshitilmayapti").
            self.route_hint.setText(
                "🎤 Exo-bekor qilish ishga tushmadi — GAPIRISH uchun CTRL "
                "(chap yoki o‘ng) tugmasini BOSIB TURING. Naushnik ulasangiz "
                "bosish shart emas."
            )
            self.route_hint.setVisible(True)
            return
        if "[AEC] ISHLADI" in line:
            self.route_hint.setText(
                "🔊 Exo-bekor qilish faol — tugma bosmasdan erkin gapiring."
            )
            self.route_hint.setVisible(True)
            return
        if "qayta ulanadi" in line:
            self._set_status("QAYTA ULANMOQDA…", "#f59e0b")
            return
        if line.startswith("[Ulanish uzildi]"):
            self.last_engine_error = line.strip("[] ")
            # SABABNI EKRANDA ko'rsatamiz. Ilgari xato faqat log faylga
            # yozilardi va foydalanuvchi "Gemini kutilmoqda / qayta ulanmoqda"
            # dan boshqa hech narsa ko'rmasdi — boshqa kompyuterda log
            # olmasdan sababni bilishning iloji yo'q edi.
            reason, is_normal = self._explain_connection_error(line)
            if not is_normal:
                self._set_status("ULANISH XATOSI", "#ef4444")
                self.route_hint.setText(reason)
                self.route_hint.setVisible(True)
            return
        if " › " not in line:
            return
        language, text = line.split(" › ", 1)
        if not text:
            return
        if channel == "OUTGOING":
            pair = self.mode_pairs["outgoing"]
        elif channel == "INCOMING":
            pair = self.mode_pairs["incoming"]
        else:
            pair = self._current_pair()
        target_code = pair.target.upper()
        captions = self.channel_captions.get(channel)
        if captions is None:
            captions = {"source": self.source_caption, "target": self.target_caption}
        route_label = {
            "INCOMING": "Meeting",
            "OUTGOING": "Siz",
        }.get(channel, "Eshitildi")
        is_target = language.upper().startswith(target_code)
        new_turn = False
        if is_target:
            captions["target"] = self._append_caption(captions["target"], text)
        else:
            new_turn = not captions["source"] or captions["source"].endswith((".", "?", "!"))
            captions["source"] = self._append_caption(captions["source"], text)
            if new_turn:
                captions["target"] = ""

        if self._current_mode() == "duplex" and channel == "OUTGOING":
            self.duplex_outgoing_caption_title.setText(
                f"Meeting’ga ketayotgan tarjima  ·  {language_caption(pair.target)}"
            )
            self.duplex_outgoing_original_text.setText(
                f"Siz: {captions['source'] or 'gap kutilmoqda…'}"
            )
            self.duplex_outgoing_target_text.setText(
                f"Tarjima: {captions['target'] or 'tayyorlanmoqda…'}"
            )
            return

        if is_target:
            self.target_language.setText(
                f"{route_label}  ·  Tarjima  ·  {language_caption(pair.target)}"
            )
            self.target_text.setText(captions["target"])
        else:
            self.source_language.setText(
                f"{route_label}  ·  {language.upper()}"
            )
            self.source_text.setText(captions["source"])
            if new_turn:
                self.target_language.setText(
                    f"{route_label}  ·  Tarjima  ·  {language_caption(pair.target)}"
                )
                self.target_text.setText("Tarjima qilinmoqda…")
        self.source_caption = captions["source"]
        self.target_caption = captions["target"]

    @staticmethod
    def _append_caption(current: str, chunk: str) -> str:
        text = chunk if not current or current.endswith((".", "?", "!")) else f"{current} {chunk}"
        return ("…" + text[-139:].lstrip()) if len(text) > 140 else text

    def _process_finished(self, exit_code: int, _status) -> None:  # noqa: ANN001
        stop_requested = self.stop_requested
        self.stop_requested = False
        self.connection_timer.stop()
        self.engine_log_timer.stop()
        self.device_change_timer.stop()
        self.heartbeat_timer.stop()
        self.heartbeat_in_progress = False
        self._read_engine_log()
        process = self.process
        if process:
            process.readAllStandardOutput()
        if self.output_buffer.strip():
            self._handle_line(self.output_buffer.strip())
        self.output_buffer = ""
        self.process = None
        if process:
            process.deleteLater()
        self._end_control_session()
        self._restore_system_audio()
        self._set_controls(running=False)
        if is_expected_engine_exit(exit_code, stop_requested):
            self._set_status("TO‘XTADI", "#94a3b8")
            # To'xtaganda dialog default holatiga qaytadi.
            self.source_language.setText("Suhbatdoshingiz gapiradi")
            self.source_text.setText("Gap kutilmoqda…")
            return
        detail = self.last_engine_error or self.process_error or "Dvijok kutilmaganda yopildi."
        self._set_status(self._friendly_engine_error(detail), "#ef4444")
        self.source_language.setText("Suhbatdoshingiz gapiradi")
        self.source_text.setText(detail[:180])

    @staticmethod
    def _friendly_engine_error(detail: str) -> str:
        folded = detail.casefold()
        if any(word in folded for word in ("litsenziya", "license", "revoked")):
            return "LITSENZIYA XATOSI"
        if "12 soniya ichida javob bermadi" in folded:
            return "GEMINI JAVOB BERMADI"
        if "allaqachon ishlayapti" in folded:
            return "BOSHQA NUSXA ISHLAYAPTI"
        if "api key" in folded or "api_key" in folded or "401" in folded or "403" in folded:
            return "API KEY XATOSI"
        if any(word in folded for word in ("blackhole", "audio", "portaudio", "device")):
            return "AUDIO QURILMA XATOSI"
        if any(word in folded for word in ("network", "socket", "websocket", "connect")):
            return "INTERNET / GEMINI XATOSI"
        return "ULANISH XATOSI — QAYTA BOSING"

    def _connection_timed_out(self) -> None:
        if not self.process or self.connected:
            return
        self._set_status("GEMINI KUTILMOQDA…", "#f59e0b")
        self.last_engine_error = (
            "Gemini hozir javob bermayapti. Dastur avtomatik qayta ulanadi."
        )
        # The engine owns exponential reconnect/backoff. Do not kill it while
        # the remote gateway is temporarily unavailable; keep the UI alive and
        # surface a waiting state until a later connection succeeds.
        self.connection_timer.start(30_000)

    def _output_preference_words(self) -> tuple[str, ...]:
        """Tarjima chiqishi uchun afzallik ro'yxati.

        Birinchi o'rinda TIZIM tanlagan fizik qurilma turadi: foydalanuvchi
        Bluetooth naushnigini ulaganda macOS uni o'zi tanlaydi, ya'ni nomi
        "P2961" yoki "JBL TUNE" bo'lsa ham to'g'ri topiladi. Kalit so'zlar
        faqat zaxira (tizim tanlovi virtual kabel bo'lib qolgan hollar).
        """
        words: list[str] = []
        # Foydalanuvchi tanlovi eng oldinda (qo'shimcha himoya).
        picked = str(getattr(self, "preferred_output_name", "") or "")
        if picked:
            words.append(picked.casefold())
        # Windows tinglashda tizim default'i KABELGA o'rnatiladi, shuning
        # uchun preferred_physical_output() None qaytaradi va "headphone"
        # kaliti bo'sh quloqchin uyasini tanlab qo'yardi. Start'dan oldingi
        # haqiqiy karnay (win_prev_render) — eng ishonchli belgi, uni ENG
        # OLDINGA qo'yamiz.
        prev = getattr(self, "win_prev_render", "")
        if prev and not is_virtual_device(prev):
            words.append(prev.casefold())
        try:
            choice = preferred_physical_output()
        except Exception:
            choice = None
        if choice is not None:
            words.append(choice.name.casefold())
        if platform.system() == "Windows":
            # Desktop: Realtek quloqchin uyasi ("Headphones") ko'pincha BO'SH
            # bo'lsa ham "active" ko'rinadi. Shuning uchun haqiqiy karnayni
            # ("speaker"/"realtek") quloqchindan OLDIN qo'yamiz — aks holda
            # tarjima jim uyaga chiqib, foydalanuvchi eshitmasdi.
            words.extend(("speaker", "realtek", "built-in"))
        words.extend(
            (
                "airpods",
                "headphone",
                "headset",
                "external",
                "usb",
                "macbook air speakers",
                "speaker",
                "built-in",
            )
        )
        return tuple(words)

    @staticmethod
    def _is_headphone_output(name: str) -> bool:
        """Chiqish qurilmasi naushnik/garnituramı — echo xavfi yo'q, ikki
        tomonlamada feedback-gate kerak emas."""
        n = (name or "").casefold()
        return any(
            marker in n
            for marker in (
                "headphone", "headset", "quloqchin", "наушник", "гарнитур",
                "airpods", "earbuds", " buds", "hands-free", "handsfree",
            )
        )

    @staticmethod
    def _is_handsfree_mic(name: str) -> bool:
        """Bluetooth garnituraning «hands-free» mikrofonimi?

        Windows bunday mikrofonni ochganda BT qurilma HFP (telefon) rejimiga
        o'tadi: stereo chiqish uzilib, ovoz telefon sifatiga tushadi. Boshqa
        mikrofon bo'lsa uni tanlaymiz (_select_device_kind avoid_words), lekin
        boshqa iloj bo'lmasa foydalanuvchini ogohlantiramiz."""
        n = (name or "").casefold()
        return any(
            marker in n
            for marker in ("hands-free", "handsfree", "hands free", "bluetooth")
        )

    def _output_is_ear_safe(self, name: str) -> bool:
        """Tarjima QULOQQA chiqadimi (naushnik/garnitura) — echo xavfi yo'qmi?

        True bo'lsa gapirish mikrofoni erkin ochiq qoladi; False bo'lsa
        himoya kerak (Windows: AEC, zaxira push-to-talk; macOS: gate).

        Windows'ning O'Z ma'lumotiga (endpoint FormFactor) tayanamiz: u
        qurilma naushnikmi, karnaymi yoki HDMI-monitormi ekanini aniq
        aytadi. Ilgari faqat NOM bo'yicha taxmin qilinardi va "Динамики
        (P2961)" (monitor karnayi), "Speakers (USB Audio)", HDMI/TV kabi
        HAQIQIY karnaylar "naushnik" deb hisoblanib, himoyasiz qolar edi —
        mikrofon karnaydagi tarjimani qayta ushlab CHEKSIZ HALQA hosil
        qilardi. Nom bo'yicha moslik esa QO'SHIMCHA belgi bo'lib qoladi:
        FormFactor o'qilmasa yoki "naushnik" desa — quloqqa deb sanaymiz."""
        if self._is_headphone_output(name):
            return True
        if platform.system() == "Windows":
            try:
                from winaec import output_is_ear_safe

                verdict = output_is_ear_safe(name)
                print(f"[ROUTING] formfactor {name!r} -> ear_safe={verdict}", flush=True)
                if verdict is not None:
                    return verdict
            except Exception as error:
                print(f"[ROUTING] formfactor xato: {error}", flush=True)
        # Aniqlab bo'lmadi — XAVFSIZ tomonni tanlaymiz: karnay deb hisoblab
        # himoyani yoqamiz (echo halqasi eng yomon nosozlik edi).
        return False

    @staticmethod
    def _explain_connection_error(raw: str) -> tuple[str, bool]:
        """Dvigatel xatosini ODDIY tilda tushuntiradi.

        Qaytaradi: (tushuntirish, normal_holatmi). "normal" — Gemini
        sessiyasining muddati tugab, ilova o'zi qayta ulanayotgan holat:
        bu nosozlik emas, foydalanuvchini qo'rqitmaymiz."""
        text = (raw or "").lower()
        if "goaway" in text or "session dur" in text:
            return ("", True)  # sessiya muddati — ilova o'zi qayta ulanadi
        if "certificate" in text or "ssl" in text or "certif" in text:
            return (
                "🔒 Sertifikat xatosi: kompaniya tarmog‘i internet trafigini "
                "tekshiryapti. IT bo‘limidan «generativelanguage.googleapis.com» "
                "ga ruxsat so‘rang (yoki kompaniya sertifikatini Windows’ga "
                "o‘rnatishlarini ayting).",
                False,
            )
        if any(
            marker in text
            for marker in ("getaddrinfo", "name or service", "nodename", "dns")
        ):
            return ("🌐 Internet yo‘q yoki DNS ishlamayapti.", False)
        if any(
            marker in text
            for marker in ("timed out", "timeout", "1006", "refused", "unreachable")
        ):
            return (
                "🚧 Tarmoq to‘sig‘i (firewall/proxy): Gemini serveriga yetib "
                "bo‘lmadi. IT bo‘limidan «generativelanguage.googleapis.com» "
                "(443-port, WebSocket) ga ruxsat so‘rang.",
                False,
            )
        if any(
            marker in text
            for marker in ("api key", "api_key", "permission_denied", "401", "403", "unauthenticated")
        ):
            return ("🔑 API kalit noto‘g‘ri yoki ruxsat berilmagan.", False)
        if any(
            marker in text
            for marker in (
                "credits are depleted", "prepayment", "billing", "1011",
                "payment", "insufficient funds",
            )
        ):
            return (
                "💳 Gemini hisobidagi PUL (kredit) tugagan. ai.studio/projects "
                "ga kirib hisobni to‘ldiring — shundan keyin tarjima darhol "
                "ishlaydi (dasturni qayta o‘rnatish shart emas).",
                False,
            )
        if any(
            marker in text
            for marker in ("resource_exhausted", "quota", "429", "rate limit")
        ):
            return ("📉 Gemini kvotasi tugagan yoki so‘rovlar chegarasi.", False)
        short = (raw or "").strip()
        return (f"⚠️ Ulanish xatosi: {short[:160]}", False)

    def ask_output_device(self, forced: bool = False) -> None:
        """Ovoz qurilmasini BIR MARTA so'raydi (yoki menyudan chaqiriladi)."""
        if platform.system() == "Darwin" and not forced:
            return
        if not forced and str(self.settings.value("preferred_output", "") or ""):
            return  # allaqachon tanlangan
        try:
            devices = [
                device
                for device in available_devices("output")
                if not is_virtual_device(device.name)
                # SOXTA QURILMALAR ("Переназначение звуковых устр. - Output",
                # "Sound Mapper", "Первичный звуковой драйвер") ro'yxatdan
                # CHIQARILADI. Jonli nosozlik (2026-07-29, uy kompyuteri):
                # foydalanuvchi shuni tanlagan edi -> aks-sado bekor qilish
                # qurilmani topolmay o'ldi (karnay=-1) va dastur "Ctrl bosib
                # gapiring" rejimiga tushdi, ya'ni gapirish ishlamay qoldi;
                # tarjima ovozi esa yo'naltirgich orqali TIZIM chiqishiga
                # ketdi — u esa o'sha payt virtual kabelga o'zgartirilgan
                # bo'ladi, demak eshitilmaydi.
                and not is_alias_output(device.name)
            ]
        except Exception:
            return
        if len(devices) < 2 and not forced:
            return  # tanlashga narsa yo'q — bitta chiqish bor
        # MUHIM: oyna MODAL BO'LMASLIGI kerak. 0.9.61 da modal `exec()`
        # ishlatilgandi va u asosiy oyna ORQASIDA ochilib, butun ilovani
        # bloklab qo'ydi — foydalanuvchida Start ham, Stop ham bosilmadi
        # (log: "Ilova jurnali" bo'm-bo'sh, hech qanday [ROUTING] yo'q).
        # Endi oddiy oyna: ochiq tursa ham ilova ishlayveradi.
        existing = getattr(self, "_output_picker", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = OutputPickerDialog(
            devices, str(getattr(self, "preferred_output_name", "")), self
        )
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.accepted.connect(lambda d=dialog: self._output_picked_from_dialog(d))
        self._output_picker = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _output_picked_from_dialog(self, dialog) -> None:  # noqa: ANN001
        chosen = getattr(dialog, "chosen", "")
        if not chosen:
            return
        self.preferred_output_name = chosen
        self.settings.setValue("preferred_output", chosen)
        position = self.output_device.findText(chosen)
        if position >= 0:
            self.output_device.setCurrentIndex(position)
        self.route_hint.setText(f"🔈 Tarjima ovozi «{chosen}» qurilmasidan chiqadi.")
        self.route_hint.setVisible(True)
        print(f"[ROUTING] dialogdan tanlandi: {chosen!r}", flush=True)

    def _play_output_test(self) -> None:
        """Tanlangan chiqishga qisqa sinov ovozi (0.6s) chiqaradi.

        Foydalanuvchi Start bosmasdan «shu qurilmadan eshitamanmi?» degan
        savolga javob oladi — bir necha marta takrorlangan "tarjima matni
        ko'rinadi, ovozi eshitilmaydi" nosozligining asosiy sababi shu edi."""
        index = self.output_device.currentData()
        name = self._device_name(self.output_device)
        if index is None:
            self.route_hint.setText("Avval chiqish qurilmasini tanlang.")
            self.route_hint.setVisible(True)
            return
        try:
            import numpy as np
            import sounddevice as sd

            rate = 48_000
            t = np.arange(int(rate * 0.6), dtype=np.float32) / rate
            # Ikki ohang (do–mi): karnay sinoviga xos, quloqqa yoqimli.
            tone = 0.25 * np.sin(2 * np.pi * 523.25 * t)
            tone[len(t) // 2 :] = 0.25 * np.sin(
                2 * np.pi * 659.25 * t[: len(t) - len(t) // 2]
            )
            fade = np.linspace(0.0, 1.0, 400, dtype=np.float32)
            tone[: len(fade)] *= fade
            tone[-len(fade) :] *= fade[::-1]
            sd.play(tone, samplerate=rate, device=int(index), blocking=False)
            self.route_hint.setText(
                f"🔈 Sinov ovozi «{name}» qurilmasiga yuborildi. Eshitilmasa — "
                "ro‘yxatdan boshqa qurilmani tanlab, yana bosing."
            )
            self.route_hint.setVisible(True)
        except Exception as error:
            self.route_hint.setText(f"Sinov ovozi chiqmadi: {error}")
            self.route_hint.setVisible(True)

    def _output_device_picked(self, _index: int) -> None:
        """Foydalanuvchi tarjima ovozi chiqadigan qurilmani O'ZI tanladi.

        Shundan keyin avtomatik tanlov (tizim default'i / naushnik) BEKOR
        qilinadi — odam qayerdan eshitayotganini faqat o'zi biladi. Tanlov
        keyingi ochilishlarga ham saqlanadi."""
        name = self._device_name(self.output_device)
        if not name or is_virtual_device(name):
            return  # virtual kabel tanlansa — avtomatik rejim qoladi
        self.preferred_output_name = name
        self.settings.setValue("preferred_output", name)
        print(f"[ROUTING] foydalanuvchi chiqishni tanladi: {name!r}", flush=True)

    def _connected_headphone_name(self) -> str:
        """ULANGAN naushnik/garnitura nomi (Windows), bo'lmasa "".

        Foydalanuvchi talabi: «naushnik ulangan bo'lsa naushnikka bersin,
        bo'lmasa Realtek karnayga». Windows faqat ULANGAN qurilmani ACTIVE
        deb belgilaydi (bo'sh quloqchin uyasi ACTIVE emas), FormFactor esa
        uning naushnik ekanini aytadi — shuning uchun bu belgi ishonchli.
        Bluetooth naushnik ham shu yo'l bilan topiladi."""
        if platform.system() != "Windows":
            return ""
        try:
            from winaec import active_endpoints, FF_EAR_SAFE

            for name, form_factor in active_endpoints(0):
                if not name or is_virtual_device(name):
                    continue
                if form_factor in FF_EAR_SAFE or self._is_headphone_output(name):
                    return name
        except Exception as error:
            print(f"[ROUTING] naushnik qidiruvi xato: {error}", flush=True)
        return ""

    def _aec_reference_output(self) -> str:
        """Aks-sado bekor qilish uchun ORIENTIR qurilma.

        Bu foydalanuvchi AYNAN eshitayotgan chiqish — «Meeting o'zbekcha»
        rejimida meeting ovozi shundan yangraydi va mikrofon shuni eshitadi.
        Tartib: «ESHITAMAN» dagi tanlov → ulangan naushnik → tizim default'i
        (bu rejimda default o'zgartirilmaydi, shuning uchun ishonchli)."""
        chosen = getattr(self, "preferred_output_name", "")
        if chosen and not is_virtual_device(chosen):
            return chosen
        headphone = self._connected_headphone_name()
        if headphone:
            return headphone
        current = self._current_default_output()
        if current and not is_virtual_device(current):
            return current
        return self._physical_output_name()

    def _physical_output_name(self) -> str:
        """Nazorat ovozi uchun virtual bo'lmagan chiqish (tizim tanlovi afzal)."""
        # ULANGAN naushnik doim ustun: tarjima quloqqa borishi kerak (aks
        # holda tizim default'i karnayda qolib, naushnikda jimlik bo'lardi).
        headphone = self._connected_headphone_name()
        if headphone:
            return headphone
        devices = [
            device
            for device in available_devices("output")
            if not is_virtual_device(device.name)
        ]
        for keyword in self._output_preference_words():
            for device in devices:
                if keyword in device.name.casefold():
                    return device.name
        return devices[0].name if devices else ""

    @staticmethod
    def _output_device_signature() -> tuple[str, ...]:
        """Joriy chiqish qurilmalari ro'yxati (PortAudio yangilangan holda).

        GUI'da ochiq audio oqim yo'q, shuning uchun PortAudio'ni qayta
        yuklash xavfsiz — dvigatel esa alohida jarayonda ishlaydi.
        """
        try:
            sd._terminate()  # type: ignore[attr-defined]
            sd._initialize()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            return tuple(
                sorted(
                    str(device["name"])
                    for device in sd.query_devices()
                    if int(device["max_output_channels"]) > 0
                )
            )
        except Exception:
            return ()

    def _check_device_changes(self) -> None:
        """Windows: naushnik ulansa/uzilsa sessiyani qayta ulaydi.

        Windows'da yangi qurilma ulanganda PortAudio ro'yxati eskiradi va
        index'lar suriladi — ochiq oqim jim qolib, tarjima to'xtab qolardi.
        Yangi jarayon esa qurilmalarni toza sanaydi, shuning uchun eng
        ishonchli yechim — sessiyani qayta ulash.
        """
        if self.process is None or platform.system() == "Darwin":
            return
        # DIQQAT: qurilmalarni sanash PortAudio'ni QAYTA YUKLAYDI va
        # Windows'da 0.5-3 SONIYA oladi. Ilgari bu GUI oqimida, har 3
        # soniyada bajarilardi — ilova muntazam QOTIB turardi (foydalanuvchi:
        # "asabni buzib yuboryapti"). Endi fon oqimida, natija signal orqali.
        if self._scan_busy:
            return
        self._scan_busy = True
        threading.Thread(target=self._scan_signature_worker, daemon=True).start()

    def _scan_signature_worker(self) -> None:
        try:
            with self._sd_lock:
                signature = self._output_device_signature()
        except Exception:
            signature = ()
        self.device_scan_signals.signature.emit(signature)

    def _device_signature_ready(self, signature: tuple) -> None:
        self._scan_busy = False
        if self.process is None:
            return
        if not signature or signature == self.device_signature:
            return
        self.device_signature = signature
        # Yangi qurilmani dvigatelga BILDIRAMIZ (sessiyani uzmasdan):
        # dvigatel faylni o'qib, oqimlarni yangi qurilmada qayta ochadi.
        # Gemini ulanishi saqlanadi — uzilish ~1 soniya.
        mode = self._current_mode()
        if mode == "outgoing":
            return  # chiqish virtual kabel — almashtirilmaydi
        desired = self._physical_output_name()
        if not desired:
            return
        try:
            # BIRLASHTIRIB yozamiz: to'g'ridan-to'g'ri yozsak `incoming_paused`
            # buyrug'i o'chib, «Meeting o'zbekcha» o'z-o'zidan qaytib qolardi.
            self._write_engine_command(output=desired)
        except OSError:
            return
        self._set_status("AUDIO QURILMA ALMASHDI", "#f59e0b")
        self.route_hint.setText(f"Tarjima ovozi «{desired}» qurilmasiga ko‘chirilmoqda…")

    def _ensure_physical_defaults(self, force: bool = False) -> None:
        """Tarjima ISHLAMAYOTGANDA tizim mikrofoni/karnayi FIZIK bo'lishi shart.

        Aks holda Meet/Zoom "Same as System" bilan bo'sh kabelni tinglaydi va
        foydalanuvchini hech kim eshitmaydi (O'ktam Meet'da aynan shu holatga
        tushdi: mikrofon eski sessiyadan BlackHole 16ch'da qolgan edi).

        Hayot sikli: o'chiq = fizik → Start = kerakli kabel → Stop = fizik.
        """
        if platform.system() != "Darwin":
            return
        if self.process is not None and not force:
            return
        with suppress(Exception):
            current_input = system_default_input().name
            if is_virtual_device(current_input):
                physical = next(
                    (
                        device
                        for device in available_devices("input")
                        if not is_virtual_device(device.name)
                    ),
                    None,
                )
                if physical is not None:
                    route_input_to(physical.name)
        with suppress(Exception):
            current_output = system_default_output().name
            if is_virtual_device(current_output):
                physical_output = next(
                    (
                        device
                        for device in available_devices("output")
                        if not is_virtual_device(device.name)
                        and not is_alias_output(device.name)
                    ),
                    None,
                )
                if physical_output is not None:
                    route_output_to(physical_output.name)

    def _restore_physical_microphone(self) -> None:
        """Menyu paneldagi qo'lda tiklash tugmasi (zaxira yo'l)."""
        if platform.system() != "Darwin" or self.process is not None:
            return
        try:
            current = system_default_input()
            if not is_virtual_device(current.name):
                return
            physical = next(
                (
                    device
                    for device in available_devices("input")
                    if not is_virtual_device(device.name)
                ),
                None,
            )
            if physical is None:
                return
            # DIQQAT: available_devices() PortAudio indeksini beradi, CoreAudio
            # device_id emas — ularni aralashtirsa boshqa qurilma tanlanadi.
            # route_input_to() nom bo'yicha CoreAudio'dan qidiradi.
            route_input_to(physical.name)
            self.route_hint.setText(
                f"Tizim mikrofoni «{current.name}» dan «{physical.name}» ga qaytarildi."
            )
        except Exception:
            # Tiklash ixtiyoriy qulaylik — xatosi ilovani to'xtatmasin.
            pass

    def _restore_system_audio(self) -> None:
        previous_output = self.previous_system_output
        previous_input = self.previous_system_input
        self.previous_system_output = None
        self.previous_system_input = None
        if platform.system() == "Windows":
            # Stop: Start'da saqlangan default qurilmalarni qaytaramiz.
            # Odatda FON oqimida (GUI qotmasin), LEKIN ilova yopilayotgan
            # bo'lsa tugashini kutamiz — aks holda tiklash bajarilmay qoladi.
            self._win_restore_routing_async(
                blocking=bool(getattr(self, "quit_requested", False))
            )
            return
        if platform.system() != "Darwin":
            return
        errors: list[str] = []
        # Himoya: oldingi crash tufayli "previous" ning o'zi kabel bo'lib
        # qolgan bo'lishi mumkin — unga qaytarish foydasiz, fizikka
        # qaytaramiz (_ensure_physical_defaults pastda).
        if previous_input and not is_virtual_device(previous_input.name):
            try:
                set_default_input(previous_input)
            except Exception as error:
                errors.append(f"Avvalgi microphone qaytarilmadi: {error}")
        if previous_output and not is_virtual_device(previous_output.name):
            try:
                set_default_output(previous_output)
            except Exception as error:
                errors.append(f"Avvalgi audio output qaytarilmadi: {error}")
        self._ensure_physical_defaults(force=True)
        if errors:
            restored_error = " | ".join(errors)
            self.last_engine_error = " | ".join(
                value for value in (self.last_engine_error, restored_error) if value
            )

    def _end_control_session(self) -> None:
        client = self.license_client
        if not client or not client.enabled or not client.session_id:
            return

        def finish() -> None:
            try:
                client.end_session()
            except Exception:
                pass

        threading.Thread(target=finish, daemon=True).start()

    # SURISH OLIB TASHLANDI (2026-07-30 foydalanuvchi talabi): oyna tizim
    # paneli (Quick Settings) kabi burchakka yopishgan holda QOTIB turadi —
    # "siljitib bo'lmasin, o'sha yerda qotsin, doim shunday bo'lsin".
    # Windows'da har ko'rsatilganda `showEvent` uni soat yonidagi burchakka
    # qaytaradi.

    def showEvent(self, event) -> None:  # noqa: ANN001
        if platform.system() == "Windows":
            self._position_near_tray()
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        tray = getattr(self, "tray", None)
        if self.process is not None and not self.quit_requested and tray is not None:
            # Jonli tarjimani oyna yopilgani uchun uzmaymiz — ilova menyu
            # panelida davom etadi. Butunlay chiqish: tray > Chiqish.
            event.ignore()
            self.hide()
            pass  # bildirishnoma olib tashlandi (foydalanuvchi talabi)
            return
        self.heartbeat_timer.stop()
        if self.process:
            self.stop_requested = True
            self.process.kill()
            self.process.waitForFinished(2000)
        self._end_control_session()
        self._restore_system_audio()
        if tray is not None:
            tray.hide()
        event.accept()
        # setQuitOnLastWindowClosed(False) tray uchun kerak — demak
        # oyna yopilganda chiqishni o'zimiz chaqiramiz.
        QApplication.quit()


def log_directory() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / APP_NAME


def setup_app_logging() -> Path:
    """GUI jarayonining hamma chiqishini faylga yozadi.

    Windows'da windowed .exe'ning stdout/stderr'i yo'q — xato yuz bersa
    hech qayerda iz qolmasdi. Endi app.log ichida diagnostika sarlavhasi,
    barcha print'lar va ushlanmagan istisnolar (traceback) saqlanadi.
    """
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "app.log"
    try:
        log_file = path.open("w", encoding="utf-8", buffering=1)
    except OSError:
        return path

    class _Tee:
        def __init__(self, stream, mirror) -> None:  # noqa: ANN001
            self.stream = stream
            self.mirror = mirror

        def write(self, data: str) -> int:
            self.mirror.write(data)
            if not self.stream:
                return len(data)
            try:
                return self.stream.write(data)
            except (UnicodeEncodeError, ValueError, OSError):
                return len(data)

        def flush(self) -> None:
            self.mirror.flush()
            if not self.stream:
                return
            try:
                self.stream.flush()
            except (ValueError, OSError):
                pass

    sys.stdout = log_file if sys.stdout is None else _Tee(sys.stdout, log_file)
    sys.stderr = log_file if sys.stderr is None else _Tee(sys.stderr, log_file)

    def log_uncaught(kind, value, trace) -> None:  # noqa: ANN001
        import traceback

        print("=== USHLANMAGAN XATO ===", file=sys.stderr)
        traceback.print_exception(kind, value, trace, file=sys.stderr)

    sys.excepthook = log_uncaught
    print(f"=== {APP_NAME} {APP_VERSION} ({APP_EDITION}) ===")
    print(f"Vaqt      : {datetime.now().isoformat(timespec='seconds')}")
    print(f"OS        : {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python    : {platform.python_version()} | frozen={getattr(sys, 'frozen', False)}")
    print(f"Log papka : {directory}")
    try:
        import sounddevice as _sd

        print("--- Audio qurilmalar ---")
        for index, device in enumerate(_sd.query_devices()):
            print(
                f"  [{index}] {device['name']} "
                f"(in={device['max_input_channels']}, out={device['max_output_channels']}, "
                f"{int(device['default_samplerate'])} Hz)"
            )
    except Exception as error:
        print(f"Audio qurilmalarni o‘qib bo‘lmadi: {error}")
    print("--- Ilova jurnali ---", flush=True)
    return path


def run_gui() -> int:
    setup_app_logging()
    # Toza mashinada (PyInstaller bundle) tizim CA'lari ko'rinmaydi —
    # dvigatel/websockets ham shu env orqali certifi'ni oladi.
    ensure_ca_bundle_env()
    auto_start = "--autostart" in sys.argv
    if auto_start:
        sys.argv.remove("--autostart")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Live Translator")

    # Tarjima ishlayotganda oyna yopilsa ilova menyu panelida yashaydi.
    app.setQuitOnLastWindowClosed(False)
    window = TranslatorWindow()
    # macOS: sof menyu-panel rejimi — asosiy oyna ochilmaydi, hamma
    # boshqaruv tepadagi belgidan. Subtitr kerak bo'lsa "Oynani
    # ko'rsatish" bilan ochiladi. Windows'da oyna odatdagidek ko'rinadi.
    menu_bar_mode = platform.system() == "Darwin"
    if menu_bar_mode:
        window.hide()
        if window.tray is not None:
            pass  # bildirishnoma olib tashlandi (foydalanuvchi talabi)
    else:
        # Windows/Linux: oyna dastlab ko'rinadi, lekin macOS-uslub
        # ApplicationActivate filtri O'RNATILMAYDI. Windows'da tray
        # belgisini bosish (chap yoki o'ng) ilovani aktivlashtiradi —
        # filtr esa buni ushlab oynani "o'z-o'zidan" ochib yuborardi
        # (foydalanuvchi shikoyati: tray'ga bossa oyna chiqardi). Endi
        # yashirilgan oyna faqat tray menyusidagi "Oynani ko'rsatish"
        # orqali qaytariladi. macOS menu-bar rejimida ham filtr kerak
        # emas — u yerda belgi orqali boshqariladi.
        window.show()
    if auto_start:
        QTimer.singleShot(1_200, window.start_translator)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui())
