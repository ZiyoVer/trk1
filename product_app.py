"""Cross-platform desktop shell and first-run setup for Live Translator."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import sys
import tempfile
import threading
import urllib.request
import uuid
import zipfile
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
    QEvent,
    QObject,
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
    QActionGroup,
    QColor,
    QDesktopServices,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
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
    preferred_physical_output,
)
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
    route_input_to,
    route_output_to,
    set_default_input,
    set_default_output,
)


APP_NAME = "Live Translator"
APP_VERSION = "0.5.0"
KEYRING_SERVICE = "local.live-translator"
KEYRING_ACCOUNT = "edcom-api-key"
KEYRING_LICENSE_ACCOUNT = "license-key"
KEYRING_CONTROL_URL_ACCOUNT = "control-url"
KEYRING_DEVICE_ACCOUNT = "device-id"
PROJECT_DIR = Path(__file__).resolve().parent
BLACKHOLE_URL = "https://existential.audio/downloads/BlackHole2ch-0.7.1.pkg"
BLACKHOLE_SHA256 = "57b540f27a3e29c37e310e01bee0fdfab76733087e47f997ef9dccf851400dcf"
BLACKHOLE_16CH_URL = "https://existential.audio/downloads/BlackHole16ch-0.7.1.pkg"
BLACKHOLE_16CH_SHA256 = "57254e2f76cd40db7f3f715238b1a2cb2bd08819d38abf4087f2944f71a3641a"
VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VBCABLE_SHA256 = "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb"
BLACKHOLE_DRIVER_PATH = Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver")
BLACKHOLE_16CH_DRIVER_PATH = Path("/Library/Audio/Plug-Ins/HAL/BlackHole16ch.driver")


def is_engine_connected_line(line: str) -> bool:
    return "ulandi." in line.casefold()


def is_expected_engine_exit(exit_code: int, stop_requested: bool) -> bool:
    """A user-requested process exit is a normal Stop, not a crash."""

    return stop_requested or exit_code == 0


class DriverSignals(QObject):
    ready = Signal(str)
    failed = Signal(str)


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
        self.setMinimumSize(520, 405)
        self.setStyleSheet(
            """
            QDialog#apiKeyDialog { background: #0f172a; }
            QLabel { color: #f8fafc; font-size: 13px; }
            QLineEdit { background: #1e293b; color: #f8fafc; border: 1px solid #475569;
                        border-radius: 8px; padding: 11px 12px; font-size: 13px;
                        selection-background-color: #2563eb; }
            QPushButton { background: #334155; color: white; border: 0;
                          border-radius: 7px; padding: 9px 18px; font-weight: 700; }
            QPushButton:hover { background: #475569; }
            """
        )
        layout = QVBoxLayout(self)
        title = QLabel("Ulanish va litsenziya")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        info = QLabel(
            "Maxfiy qiymatlar faqat tizim Keychain/Credential Manager ichida saqlanadi."
        )
        info.setWordWrap(True)
        api_label = QLabel("GEMINI API KEY")
        api_label.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: 700;")
        self.api_input = QLineEdit(api_key)
        self.api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_input.setPlaceholderText("Google AI Studio API key")
        link = QLabel(
            "Bu kalit Gemini 3.5 Live Translate’ga ulanish uchun ishlatiladi."
        )
        link.setStyleSheet("color: #60a5fa;")
        server_label = QLabel("BOSHQARUV SERVERI")
        server_label.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: 700;")
        self.control_input = QLineEdit(control_url)
        self.control_input.setPlaceholderText("https://control.example.com — bo‘sh bo‘lsa developer mode")
        license_label = QLabel("LITSENZIYA KALITI")
        license_label.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: 700;")
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
        save_button.setText("SAQLASH")
        save_button.setStyleSheet("background: #22c55e; color: white;")
        cancel_button.setText("BEKOR QILISH")
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
        self.setFixedSize(640, 680)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.drag_offset = None
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
        self.driver_install_prompted = False
        self.driver_variant = "2ch"
        self.source_caption = ""
        self.target_caption = ""
        self.channel_captions = {
            "INCOMING": {"source": "", "target": ""},
            "OUTGOING": {"source": "", "target": ""},
        }
        self.settings = QSettings("Charon", APP_NAME)
        # "Gapirish"da tarjima virtual kabelga ketadi; nazorat ovozi uni
        # naushnikda ham eshittiradi (default: yoqiq).
        self.monitor_enabled = (
            str(self.settings.value("audio/monitor_outgoing", "false")).lower() == "true"
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
        saved_mode = str(self.settings.value("translation/active_mode", "incoming"))
        self.initial_mode = saved_mode if saved_mode in APP_MODE_BY_CODE else "incoming"
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
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(
            """
            QFrame#card { background: rgba(10, 17, 30, 248); border-radius: 16px; }
            QLabel { color: #f8fafc; }
            QComboBox { background: #162236; color: #f8fafc; border: 1px solid #2d3c54;
                        border-radius: 8px; padding: 8px 11px; min-height: 24px; }
            QComboBox:hover { border-color: #52627a; }
            QComboBox:focus { border-color: #4f83f1; }
            QComboBox:disabled { background: #111a29; color: #718096; border-color: #243147; }
            QComboBox QAbstractItemView { background: #162236; color: #f8fafc;
                                         selection-background-color: #2f6fed; border: 0; }
            QPushButton { border: 0; border-radius: 8px; padding: 9px 14px;
                          color: white; font-weight: 700; }
            QPushButton:focus { border: 1px solid #93b4ff; }
            """
        )
        root.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("CHARON")
        title.setStyleSheet("font-size: 20px; font-weight: 800; letter-spacing: 0.2px;")
        voice = QLabel("LIVE TRANSLATOR")
        voice.setStyleSheet(
            "background: #17253a; color: #a9b8cc; border-radius: 7px; "
            "padding: 4px 7px; font-size: 9px; font-weight: 700;"
        )
        self.status = QLabel("●  TAYYOR")
        self.status.setStyleSheet("color: #8fa0b7; font-size: 10px; font-weight: 700;")
        settings = QPushButton("⚙")
        settings.setAccessibleName("Sozlamalar")
        settings.setToolTip("Sozlamalar")
        settings.setFixedSize(36, 34)
        settings.setStyleSheet(
            "QPushButton { background: #162236; color: #cbd5e1; font-size: 16px; padding: 0; } "
            "QPushButton:hover { background: #202f47; color: white; } "
            "QPushButton:pressed { background: #101a2a; }"
        )
        settings.clicked.connect(self.edit_settings)
        minimize = QPushButton("–")
        minimize.setAccessibleName("Kichraytirish")
        minimize.setFixedSize(36, 34)
        minimize.setToolTip("Kichraytirish")
        minimize.setStyleSheet(
            "QPushButton { background: #162236; color: #cbd5e1; font-size: 18px; "
            "padding: 0px; border-radius: 7px; } "
            "QPushButton:hover { background: #202f47; color: white; } "
            "QPushButton:pressed { background: #101a2a; }"
        )
        minimize.clicked.connect(self._minimize_window)
        close = QPushButton("✕")
        close.setAccessibleName("Yopish")
        close.setFixedSize(36, 34)
        close.setToolTip("Yopish")
        close.setStyleSheet(
            "QPushButton { background: #1e293b; color: #f8fafc; font-size: 15px; "
            "padding: 0px; border-radius: 7px; } "
            "QPushButton:hover { background: #c93c4b; color: white; } "
            "QPushButton:pressed { background: #9f2f3b; }"
        )
        close.clicked.connect(self.close)
        header.addWidget(title)
        header.addWidget(voice)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(settings)
        header.addWidget(minimize)
        header.addWidget(close)
        layout.addLayout(header)

        self.driver_row = QFrame()
        driver_layout = QHBoxLayout(self.driver_row)
        driver_layout.setContentsMargins(10, 4, 6, 4)
        self.driver_label = QLabel("")
        self.driver_label.setWordWrap(True)
        self.driver_button = QPushButton("AUDIO DRIVER O‘RNATISH")
        self.driver_button.setStyleSheet("background: #d97706;")
        self.driver_button.clicked.connect(self.install_driver)
        driver_layout.addWidget(self.driver_label, 1)
        driver_layout.addWidget(self.driver_button)
        self.driver_row.setStyleSheet("background: rgba(180, 83, 9, 85); border-radius: 8px;")
        layout.addWidget(self.driver_row)

        direction_label = QLabel("Tarjima rejimi")
        direction_label.setStyleSheet("color: #9aa9bd; font-size: 10px; font-weight: 700;")
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
            ]
        )
        layout.addWidget(direction_label)
        direction_label.setVisible(False)
        layout.addWidget(self.direction)

        self.language_label = QLabel("Til yo‘nalishi")
        self.language_label.setStyleSheet("color: #98a8bd; font-size: 11px; font-weight: 650;")
        layout.addWidget(self.language_label)

        language_row = QHBoxLayout()
        language_row.setSpacing(8)
        source_group = QVBoxLayout()
        source_group.setSpacing(4)
        source_label = QLabel("Qaysi tildan")
        source_label.setStyleSheet("color: #8798af; font-size: 10px; font-weight: 600;")
        self.source_language_select = QComboBox()
        self.source_language_select.setToolTip(
            "Eshitiladigan til. Aralash tilli meeting uchun Avtomatikni tanlang."
        )
        for language in SOURCE_LANGUAGES:
            self.source_language_select.addItem(language.name, language.code)
        source_group.addWidget(source_label)
        source_group.addWidget(self.source_language_select)

        swap_group = QVBoxLayout()
        swap_group.setSpacing(4)
        swap_spacer = QLabel("")
        swap_spacer.setFixedHeight(11)
        self.swap_languages_button = QPushButton("⇄")
        self.swap_languages_button.setFixedSize(42, 38)
        self.swap_languages_button.setToolTip("Manba va tarjima tillarini almashtirish")
        self.swap_languages_button.setStyleSheet(
            "QPushButton { background: #20334c; color: #7dd3fc; font-size: 18px; padding: 0; } "
            "QPushButton:hover { background: #29415f; color: white; } "
            "QPushButton:pressed { background: #15263a; } "
            "QPushButton:disabled { background: #17263a; color: #52657b; }"
        )
        self.swap_languages_button.clicked.connect(self._swap_languages)
        swap_group.addWidget(swap_spacer)
        swap_group.addWidget(self.swap_languages_button)

        target_group = QVBoxLayout()
        target_group.setSpacing(4)
        target_label = QLabel("Qaysi tilga")
        target_label.setStyleSheet("color: #8798af; font-size: 10px; font-weight: 600;")
        self.target_language_select = QComboBox()
        self.target_language_select.setToolTip("Tarjima ovozi va subtitr chiqadigan til")
        for language in TARGET_LANGUAGES:
            self.target_language_select.addItem(language.name, language.code)
        target_group.addWidget(target_label)
        target_group.addWidget(self.target_language_select)

        language_row.addLayout(source_group, 1)
        language_row.addLayout(swap_group)
        language_row.addLayout(target_group, 1)
        layout.addLayout(language_row)

        self.duplex_outgoing_language_panel = QFrame()
        duplex_language_layout = QVBoxLayout(self.duplex_outgoing_language_panel)
        duplex_language_layout.setContentsMargins(0, 0, 0, 0)
        duplex_language_layout.setSpacing(4)
        duplex_language_title = QLabel("GAPIRISH TILLARI  ·  MIKROFON → ZOOM")
        duplex_language_title.setStyleSheet(
            "color: #60a5fa; font-size: 9px; font-weight: 800;"
        )
        duplex_language_layout.addWidget(duplex_language_title)
        duplex_language_row = QHBoxLayout()
        duplex_language_row.setSpacing(8)
        self.duplex_outgoing_source = QComboBox()
        self.duplex_outgoing_source.setToolTip("Siz gapiradigan til")
        for language in SOURCE_LANGUAGES:
            self.duplex_outgoing_source.addItem(language.name, language.code)
        duplex_arrow = QLabel("→")
        duplex_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        duplex_arrow.setFixedWidth(28)
        duplex_arrow.setStyleSheet("color: #7dd3fc; font-size: 16px;")
        self.duplex_outgoing_target = QComboBox()
        self.duplex_outgoing_target.setToolTip("Zoom qatnashchilari eshitadigan til")
        for language in TARGET_LANGUAGES:
            self.duplex_outgoing_target.addItem(language.name, language.code)
        duplex_language_row.addWidget(self.duplex_outgoing_source, 1)
        duplex_language_row.addWidget(duplex_arrow)
        duplex_language_row.addWidget(self.duplex_outgoing_target, 1)
        duplex_language_layout.addLayout(duplex_language_row)
        layout.addWidget(self.duplex_outgoing_language_panel)

        self.signal_label = QLabel("OVOZ YO‘LI  ·  QAYERDAN → QAYERGA")
        self.signal_label.setStyleSheet("color: #74859d; font-size: 9px; font-weight: 700;")
        layout.addWidget(self.signal_label)
        self.signal_label.setVisible(False)

        input_row = QHBoxLayout()
        input_label = QLabel("MANBA")
        input_label.setFixedWidth(76)
        input_label.setStyleSheet("color: #a9b8cc; font-size: 10px; font-weight: 700;")
        self.input_device = QComboBox()
        self.input_device.setToolTip("Tarjima qilinadigan ovoz qayerdan olinadi")
        self.input_device.currentIndexChanged.connect(self._audio_route_changed)
        input_row.addWidget(input_label)
        input_row.addWidget(self.input_device, 1)
        layout.addLayout(input_row)
        input_label.setVisible(False)
        self.input_device.setVisible(False)

        output_row = QHBoxLayout()
        output_label = QLabel("CHIQISH")
        output_label.setFixedWidth(76)
        output_label.setStyleSheet("color: #a9b8cc; font-size: 10px; font-weight: 700;")
        self.output_device = QComboBox()
        self.output_device.setToolTip("Tarjima qilingan ovoz qayerga uzatiladi")
        self.output_device.currentIndexChanged.connect(self._audio_route_changed)
        output_row.addWidget(output_label)
        output_row.addWidget(self.output_device, 1)
        layout.addLayout(output_row)
        output_label.setVisible(False)
        self.output_device.setVisible(False)

        self.duplex_outgoing_audio_panel = QFrame()
        duplex_audio_layout = QVBoxLayout(self.duplex_outgoing_audio_panel)
        duplex_audio_layout.setContentsMargins(0, 0, 0, 0)
        duplex_audio_layout.setSpacing(6)
        duplex_audio_title = QLabel("GAPIRISH OVOZ YO‘LI  ·  MIKROFON → ZOOM")
        duplex_audio_title.setStyleSheet(
            "color: #60a5fa; font-size: 9px; font-weight: 800;"
        )
        duplex_audio_layout.addWidget(duplex_audio_title)
        duplex_input_row = QHBoxLayout()
        duplex_input_label = QLabel("MIKROFON")
        duplex_input_label.setFixedWidth(76)
        duplex_input_label.setStyleSheet(
            "color: #a9b8cc; font-size: 10px; font-weight: 700;"
        )
        self.duplex_outgoing_input = QComboBox()
        self.duplex_outgoing_input.setToolTip("Sizning fizik mikrofoningiz")
        self.duplex_outgoing_input.currentIndexChanged.connect(self._audio_route_changed)
        duplex_input_row.addWidget(duplex_input_label)
        duplex_input_row.addWidget(self.duplex_outgoing_input, 1)
        duplex_audio_layout.addLayout(duplex_input_row)
        duplex_output_row = QHBoxLayout()
        duplex_output_label = QLabel("MEETING")
        duplex_output_label.setFixedWidth(76)
        duplex_output_label.setStyleSheet(
            "color: #a9b8cc; font-size: 10px; font-weight: 700;"
        )
        self.duplex_outgoing_output = QComboBox()
        self.duplex_outgoing_output.setPlaceholderText("BlackHole 16ch kerak")
        self.duplex_outgoing_output.setToolTip(
            "Zoom microphone sifatida ishlaydigan ikkinchi virtual qurilma"
        )
        self.duplex_outgoing_output.currentIndexChanged.connect(self._audio_route_changed)
        duplex_output_row.addWidget(duplex_output_label)
        duplex_output_row.addWidget(self.duplex_outgoing_output, 1)
        duplex_audio_layout.addLayout(duplex_output_row)
        layout.addWidget(self.duplex_outgoing_audio_panel)
        self.duplex_outgoing_audio_panel.setVisible(False)

        self.route_hint = QLabel("")
        self.route_hint.setWordWrap(True)
        # Zoom mikrofoni noto'g'ri bo'lsa hamma narsa ishlab tursa ham
        # suhbatdoshlar tarjimani eshitmaydi — bu ogohlantirish ko'zga
        # tashlanadigan bo'lishi kerak.
        self.route_hint.setStyleSheet(
            "color: #ffd166; background: #2a2415; border: 1px solid #4a3f1e; "
            "border-radius: 7px; padding: 7px 9px; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self.route_hint)

        self.caption_panel = QFrame()
        self.caption_panel.setObjectName("captionPanel")
        self.caption_panel.setStyleSheet(
            "QFrame#captionPanel { background: #0f1a2a; border-radius: 11px; }"
        )
        caption_layout = QVBoxLayout(self.caption_panel)
        caption_layout.setContentsMargins(13, 10, 13, 12)
        caption_layout.setSpacing(5)
        self.source_language = QLabel("Eshitildi  ·  EN")
        self.source_language.setStyleSheet("color: #8fa0b7; font-size: 9px; font-weight: 700;")
        self.source_text = QLabel("Gap kutilmoqda…")
        self.source_text.setWordWrap(True)
        self.source_text.setMinimumHeight(34)
        self.source_text.setStyleSheet("color: #d8e0eb; font-size: 13px; font-weight: 600;")
        caption_layout.addWidget(self.source_language)
        caption_layout.addWidget(self.source_text)

        self.target_language = QLabel("Tarjima  ·  O‘ZBEKCHA")
        self.target_language.setStyleSheet("color: #42d884; font-size: 9px; font-weight: 800;")
        self.target_text = QLabel("Tarjima shu yerda chiqadi…")
        self.target_text.setWordWrap(True)
        self.target_text.setMinimumHeight(38)
        self.target_text.setStyleSheet("color: #e2faec; font-size: 15px; font-weight: 700;")
        caption_layout.addWidget(self.target_language)
        caption_layout.addWidget(self.target_text)
        layout.addWidget(self.caption_panel)

        self.duplex_outgoing_caption_panel = QFrame()
        self.duplex_outgoing_caption_panel.setObjectName("duplexCaption")
        self.duplex_outgoing_caption_panel.setStyleSheet(
            "QFrame#duplexCaption { background: rgba(37, 99, 235, 35); border-radius: 8px; }"
        )
        duplex_caption_layout = QVBoxLayout(self.duplex_outgoing_caption_panel)
        duplex_caption_layout.setContentsMargins(10, 7, 10, 7)
        duplex_caption_layout.setSpacing(3)
        self.duplex_outgoing_caption_title = QLabel("Meeting’ga ketayotgan tarjima")
        self.duplex_outgoing_caption_title.setStyleSheet(
            "color: #60a5fa; font-size: 9px; font-weight: 800;"
        )
        self.duplex_outgoing_original_text = QLabel("Siz: gap kutilmoqda…")
        self.duplex_outgoing_original_text.setWordWrap(True)
        self.duplex_outgoing_original_text.setStyleSheet(
            "color: #cbd5e1; font-size: 11px; font-weight: 600;"
        )
        self.duplex_outgoing_target_text = QLabel("Tarjima: shu yerda chiqadi…")
        self.duplex_outgoing_target_text.setWordWrap(True)
        self.duplex_outgoing_target_text.setStyleSheet(
            "color: #dbeafe; font-size: 12px; font-weight: 700;"
        )
        duplex_caption_layout.addWidget(self.duplex_outgoing_caption_title)
        duplex_caption_layout.addWidget(self.duplex_outgoing_original_text)
        duplex_caption_layout.addWidget(self.duplex_outgoing_target_text)
        layout.addWidget(self.duplex_outgoing_caption_panel)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        self.start_button = QPushButton("▶  Tarjimani boshlash")
        self.start_button.setAccessibleName("Tarjimani boshlash")
        self.start_button.setMinimumHeight(46)
        self.start_button.setStyleSheet(
            "QPushButton { background: #1fbf68; font-size: 13px; } "
            "QPushButton:hover { background: #28ce75; }"
        )
        self.start_button.clicked.connect(self.start_translator)
        self.stop_button = QPushButton("■  To‘xtatish")
        self.stop_button.setAccessibleName("Tarjimani to‘xtatish")
        self.stop_button.setMinimumHeight(46)
        self.stop_button.setStyleSheet("background: #334155; color: #94a3b8;")
        self.stop_button.clicked.connect(self.stop_translator)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
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
        self._build_tray()
        self._sync_mode_ui(apply_devices=False)
        self._refresh_audio_devices()
        # DIQQAT: tizim mikrofonini ishga tushganda AVTOMATIK tiklamaymiz.
        # U virtual kabelda qolgani Zoom'ning "Same as System" rejimi bilan
        # hech narsa tanlamasdan ishlashini ta'minlaydi (O'ktamning ish
        # oqimi). Kerak bo'lsa menyu panelidan qo'lda tiklanadi.
        self._set_controls(running=False)

    # ------------------------------------------------------------------
    # Menyu paneli (macOS status bar) — oynani ochmasdan boshqarish
    # ------------------------------------------------------------------

    @staticmethod
    def _tray_pixmap(size: int = 22) -> QIcon:
        """Menyu paneli uchun template ikon (qora + shaffof).

        macOS template ikonlari yorug'/qorong'i panelga o'zi moslashadi —
        rangli ikon qo'yilsa panelda kir ko'rinadi.
        """
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#000000"))
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
        icon.setIsMask(True)
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
        self.tray_status_action = menu.addAction("Tayyor")
        self.tray_status_action.setEnabled(False)
        menu.addSeparator()
        # Oynani qaytarish — eng tepada: ilova LSUIElement (Dock'da
        # ko'rinmaydi), shuning uchun yashirilgan oynani faqat shu yerdan
        # yoki tray belgisini bosib qaytarish mumkin.
        top_show_action = menu.addAction("Oynani ko‘rsatish")
        top_show_action.triggered.connect(self._show_window)
        menu.addSeparator()
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.tray_mode_actions: list[QAction] = []
        for index, mode in enumerate(APP_MODES):
            action = menu.addAction(mode.title)
            action.setCheckable(True)
            mode_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, position=index: self._tray_mode_selected(position)
            )
            self.tray_mode_actions.append(action)
        menu.addSeparator()
        self.tray_start_action = menu.addAction("Tarjimani boshlash")
        self.tray_start_action.triggered.connect(self.start_translator)
        self.tray_stop_action = menu.addAction("Tarjimani to‘xtatish")
        self.tray_stop_action.triggered.connect(self.stop_translator)
        menu.addSeparator()
        self.tray_monitor_action = menu.addAction("Tarjimani o‘zim ham eshitay")
        self.tray_monitor_action.setCheckable(True)
        self.tray_monitor_action.setChecked(self.monitor_enabled)
        self.tray_monitor_action.toggled.connect(self._toggle_monitor)
        logs_action = menu.addAction("Loglarni yig‘ish (ZIP)")
        logs_action.triggered.connect(self.export_logs)
        if platform.system() == "Darwin":
            restore_mic_action = menu.addAction("Tizim mikrofonini tiklash")
            restore_mic_action.setToolTip(
                "Boshqa ilovalarda mikrofon jim bo'lsa: tizim mikrofonini "
                "virtual kabeldan fizik mikrofonga qaytaradi."
            )
            restore_mic_action.triggered.connect(self._restore_physical_microphone)
        menu.addSeparator()
        show_action = menu.addAction("Oynani ko‘rsatish")
        show_action.triggered.connect(self._show_window)
        quit_action = menu.addAction("Chiqish")
        quit_action.triggered.connect(self._quit_from_tray)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason) -> None:  # noqa: ANN001
        """Tray belgisi bosilganda yashirilgan oynani qaytaradi."""
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
                    "Rejimni almashtirish uchun avval tarjimani to‘xtating.",
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
            tray.showMessage(
                APP_NAME,
                "Oyna yashirildi — menyu panelidagi belgidan qaytariladi.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

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

    def _toggle_monitor(self, enabled: bool) -> None:
        self.monitor_enabled = enabled
        self.settings.setValue("audio/monitor_outgoing", "true" if enabled else "false")
        self.settings.sync()
        if self.process is not None and self.tray is not None:
            self.tray.showMessage(
                APP_NAME,
                "Keyingi ishga tushirishda qo‘llanadi.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _show_window(self) -> None:
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
        if not self._virtual_driver_name(refresh=True):
            QTimer.singleShot(300, self._begin_first_run_driver_setup)

    def _begin_first_run_driver_setup(self) -> None:
        if self.driver_install_prompted or self._virtual_driver_name(refresh=True):
            return
        self.driver_install_prompted = True
        self._set_status("AUDIO DRIVER O‘RNATILMOQDA…", "#f59e0b")
        self.install_driver()

    def edit_settings(self, _checked: bool = False, required: bool = False) -> None:
        dialog = SettingsDialog(self, self.api_key, self.control_url, self.license_key)
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
                self.route_hint.setText(
                    "Ikki yo‘nalish birga ishlaydi. Zoom/Meet microphone: "
                    f"“Same as System” yoki “{routes.outgoing_output.name}”; "
                    f"speaker: “Same as System” yoki “{routes.incoming_input.name}”."
                )
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
            raise ValueError("Kerakli audio qurilma topilmadi.")
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
        combo: QComboBox, virtual: bool, preferred_words: tuple[str, ...] = ()
    ) -> bool:
        candidates: list[tuple[int, str]] = []
        for index in range(combo.count()):
            name = str(
                combo.itemData(index, Qt.ItemDataRole.UserRole + 1) or ""
            ).casefold()
            is_virtual = is_virtual_device(name)
            if is_virtual == virtual:
                candidates.append((index, name))
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

    def _apply_direction_devices(self, mode: str) -> None:
        if not hasattr(self, "input_device") or self.process is not None:
            return
        if mode in {"incoming", "duplex"}:
            self._select_device_kind(
                self.input_device, virtual=True, preferred_words=("blackhole 2ch", "cable output")
            )
            self._select_device_kind(
                self.output_device,
                virtual=False,
                # Birinchi navbatda foydalanuvchi HOZIR eshitayotgan qurilma
                # (tizim tanlovi) — nomi "P2961" kabi notanish bo'lsa ham
                # to'g'ri topiladi. Nomga qarab tanlash faqat zaxira yo'l.
                preferred_words=self._output_preference_words(),
            )
        else:
            self._select_device_kind(
                self.input_device,
                virtual=False,
                preferred_words=("macbook air microphone", "microphone", "headset", "mic"),
            )
            self._select_device_kind(
                self.output_device,
                virtual=True,
                preferred_words=("blackhole 2ch", "cable input"),
            )
        if mode == "duplex":
            self._select_device_kind(
                self.duplex_outgoing_input,
                virtual=False,
                preferred_words=("macbook air microphone", "microphone", "headset", "mic"),
            )
            incoming_virtual_id = self.input_device.currentData()
            self._select_distinct_virtual(
                self.duplex_outgoing_output,
                int(incoming_virtual_id) if incoming_virtual_id is not None else None,
                self._device_name(self.input_device),
                preferred_words=(
                    "blackhole 16ch",
                    "blackhole 64ch",
                    "cable-b input",
                    "cable-a input",
                ),
            )
        self._audio_route_changed()

    def _refresh_driver_state(self) -> None:
        drivers = self._virtual_driver_names(refresh=self.process is None)
        driver = drivers[0] if drivers else None
        virtual_families = {virtual_device_family(name) for name in drivers}
        duplex_missing = self._current_mode() == "duplex" and len(virtual_families) < 2
        if duplex_missing:
            self.driver_variant = "16ch" if platform.system() == "Darwin" else "second"
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
        if platform.system() == "Windows" and self.driver_variant == "second":
            QDesktopServices.openUrl(QUrl("https://vb-audio.com/Cable/"))
            QMessageBox.information(
                self,
                "Ikkinchi virtual audio cable",
                "IKKALASI rejimiga base VB-CABLE’dan tashqari alohida "
                "VB-CABLE A yoki B kerak. Rasmiy VB-Audio sahifasi ochildi; "
                "o‘rnatib Windows’ni restart qiling.",
            )
            return
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
                archive = Path(tempfile.gettempdir()) / "VBCABLE_Driver_Pack45.zip"
                folder = Path(tempfile.gettempdir()) / "LiveTranslator-VBCABLE"
                self._download_verified(VBCABLE_URL, archive, VBCABLE_SHA256)
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                with zipfile.ZipFile(archive) as package:
                    package.extractall(folder)
                setup = folder / ("VBCABLE_Setup_x64.exe" if sys.maxsize > 2**32 else "VBCABLE_Setup.exe")
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
        with urllib.request.urlopen(
            url, timeout=60, context=secure_ssl_context()
        ) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            path.unlink(missing_ok=True)
            raise RuntimeError("Driver checksum mos kelmadi; o‘rnatish bekor qilindi.")

    def _driver_installer_ready(self, path: str) -> None:
        self.driver_button.setEnabled(True)
        self.driver_button.setText("AUDIO DRIVER O‘RNATISH")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        if platform.system() == "Darwin":
            variant = "BlackHole 16ch" if "16ch" in Path(path).name else "BlackHole 2ch"
            instructions = (
                f"Rasmiy {variant} installer ochildi. Continue → Install ni bosing, "
                "administrator parolini kiriting va installer so‘raganda Mac’ni "
                "qayta ishga tushiring. Restart’dan keyin Live Translator BlackHole’ni "
                "avtomatik topadi."
            )
        else:
            instructions = (
                "Rasmiy VB-CABLE installer ochildi. Install Driver ni bosing, "
                "administrator ruxsatini tasdiqlang va Windows’ni qayta ishga tushiring."
            )
        QMessageBox.information(
            self,
            "Audio driver",
            instructions,
        )

    def _driver_installer_failed(self, error: str) -> None:
        self.driver_button.setEnabled(True)
        self.driver_button.setText("QAYTA URINISH")
        QMessageBox.critical(self, "Driver o‘rnatilmadi", error)

    def _current_mode(self) -> str:
        return APP_MODES[self.direction.currentIndex()].code

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
        self.source_language.setText(f"Eshitildi  ·  {language_caption(pair.source)}")
        self.target_language.setText(f"Tarjima  ·  {language_caption(pair.target)}")
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
        self.duplex_outgoing_caption_panel.setVisible(duplex)
        self.language_label.setText(
            "Meeting sizga qanday tarjima qilinadi"
            if duplex
            else "Til yo‘nalishi"
        )
        self.setFixedSize(640, 750 if duplex else 560)
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

    def _store_duplex_outgoing_pair(self, pair: LanguagePair) -> None:
        self.mode_pairs["outgoing"] = pair
        self.settings.setValue("translation/outgoing/source", pair.source)
        self.settings.setValue("translation/outgoing/target", pair.target)
        self.settings.sync()
        self._refresh_direction_labels()
        self._sync_mode_ui(apply_devices=False)

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
        ready = bool(self.api_key and devices_ready and not self.license_check_in_progress)
        self.start_button.setEnabled(not running and ready)
        self.stop_button.setEnabled(running)
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
                "QPushButton { background: #1fbf68; color: white; font-size: 13px; } "
                "QPushButton:hover { background: #28ce75; } "
                "QPushButton:pressed { background: #169653; }"
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
        self.status.setText(f"●  {text}")
        self.status.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700;")
        status_action = getattr(self, "tray_status_action", None)
        if status_action is not None:
            status_action.setText(text.capitalize())
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.setToolTip(f"{APP_NAME} — {text.capitalize()}")

    def start_translator(self) -> None:
        if self.process is not None or self.license_check_in_progress:
            return
        if not self.api_key:
            self.edit_settings(required=True)
            return
        if self.input_device.currentData() is None or self.output_device.currentData() is None:
            self._set_status("AUDIO QURILMA TANLANG", "#ef4444")
            return
        if self._current_mode() == "duplex":
            try:
                validate_duplex_routes(self._duplex_routes())
            except (TypeError, ValueError) as error:
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
        process_arguments = ["--voice", "Charon"]
        control_sessions: list[tuple[str, str, str, str, str]] = []
        try:
            if mode == "duplex":
                routes = self._duplex_routes()
                validate_duplex_routes(routes)
                incoming_pair = self.mode_pairs["incoming"]
                outgoing_pair = self.mode_pairs["outgoing"]
                process_arguments.extend(
                    [
                        "--duplex",
                        "--incoming-source-language",
                        incoming_pair.source,
                        "--incoming-target-language",
                        incoming_pair.target,
                        "--incoming-input-device",
                        str(routes.incoming_input.index),
                        "--incoming-output-device",
                        str(routes.incoming_output.index),
                        "--outgoing-source-language",
                        outgoing_pair.source,
                        "--outgoing-target-language",
                        outgoing_pair.target,
                        "--outgoing-input-device",
                        str(routes.outgoing_input.index),
                        "--outgoing-output-device",
                        str(routes.outgoing_output.index),
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
                if platform.system() == "Darwin":
                    self.previous_system_output = route_output_to(
                        routes.incoming_input.name
                    )
                    self.previous_system_input = route_input_to(
                        routes.outgoing_output.name
                    )
            else:
                input_id = int(self.input_device.currentData())
                output_id = int(self.output_device.currentData())
                input_name = self._device_name(self.input_device)
                output_name = self._device_name(self.output_device)
                input_virtual = is_virtual_device(input_name)
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
                pair = self._current_pair()
                process_arguments.extend(
                    [
                        "--target-language",
                        pair.target,
                        "--source-language",
                        pair.source,
                        "--input-device",
                        str(input_id),
                        "--output-device",
                        str(output_id),
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
            self.source_language.setText("TEXNIK HOLAT")
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
            self.device_change_timer.start()
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
        if self.process is None:
            return
        self.stop_requested = True
        self._set_status("TO‘XTATILMOQDA…", "#f59e0b")
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

    def _handle_line(self, line: str) -> None:
        channel = ""
        if line.startswith("[") and "] " in line:
            candidate, remainder = line[1:].split("] ", 1)
            if candidate in {"INCOMING", "OUTGOING"}:
                channel = candidate
                line = remainder
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
        if "qayta ulanadi" in line:
            self._set_status("QAYTA ULANMOQDA…", "#f59e0b")
            return
        if line.startswith("[Ulanish uzildi]"):
            self.last_engine_error = line.strip("[] ")
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
            # Oldingi muvaffaqiyatsiz urinishdan qolgan "TEXNIK HOLAT"
            # kartasi normal to'xtashdan keyin turib qolmasin.
            if self.source_language.text() == "TEXNIK HOLAT":
                self.source_language.setText("Suhbatdoshingiz gapiradi")
                self.source_text.setText("Gap kutilmoqda…")
            return
        detail = self.last_engine_error or self.process_error or "Dvijok kutilmaganda yopildi."
        self._set_status(self._friendly_engine_error(detail), "#ef4444")
        self.source_language.setText("TEXNIK HOLAT")
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

    @staticmethod
    def _output_preference_words() -> tuple[str, ...]:
        """Tarjima chiqishi uchun afzallik ro'yxati.

        Birinchi o'rinda TIZIM tanlagan fizik qurilma turadi: foydalanuvchi
        Bluetooth naushnigini ulaganda macOS uni o'zi tanlaydi, ya'ni nomi
        "P2961" yoki "JBL TUNE" bo'lsa ham to'g'ri topiladi. Kalit so'zlar
        faqat zaxira (tizim tanlovi virtual kabel bo'lib qolgan hollar).
        """
        words: list[str] = []
        try:
            choice = preferred_physical_output()
        except Exception:
            choice = None
        if choice is not None:
            words.append(choice.name.casefold())
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

    def _physical_output_name(self) -> str:
        """Nazorat ovozi uchun virtual bo'lmagan chiqish (tizim tanlovi afzal)."""
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
        signature = self._output_device_signature()
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
            self.device_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.device_state_path.write_text(
                json.dumps({"output": desired}), encoding="utf-8"
            )
        except OSError:
            return
        self._set_status("AUDIO QURILMA ALMASHDI", "#f59e0b")
        self.route_hint.setText(f"Tarjima ovozi «{desired}» qurilmasiga ko‘chirilmoqda…")

    def _restore_physical_microphone(self) -> None:
        """Tizim mikrofonini virtual kabeldan fizik mikrofonga qaytaradi.

        QO'LDA chaqiriladi (menyu panel). Avtomatik qilmaymiz: kabelda
        qolgani Zoom "Same as System" bilan hech narsa tanlamasdan
        ishlashini ta'minlaydi. Lekin tarjimon o'chiq bo'lganda boshqa
        ilovalarda mikrofon jim bo'ladi — o'shanda shu tugma yordam beradi.
        """
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
        if platform.system() != "Darwin":
            return
        errors: list[str] = []
        if previous_input:
            try:
                set_default_input(previous_input)
            except Exception as error:
                errors.append(f"Avvalgi microphone qaytarilmadi: {error}")
        if previous_output:
            try:
                set_default_output(previous_output)
            except Exception as error:
                errors.append(f"Avvalgi audio output qaytarilmadi: {error}")
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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.drag_offset = None

    def closeEvent(self, event) -> None:  # noqa: ANN001
        tray = getattr(self, "tray", None)
        if self.process is not None and not self.quit_requested and tray is not None:
            # Jonli tarjimani oyna yopilgani uchun uzmaymiz — ilova menyu
            # panelida davom etadi. Butunlay chiqish: tray > Chiqish.
            event.ignore()
            self.hide()
            tray.showMessage(
                APP_NAME,
                "Tarjima davom etmoqda — menyu panelidan boshqaring.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
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
    print(f"=== {APP_NAME} {APP_VERSION} ===")
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

    class _ActivateFilter(QObject):
        """Ilova ustiga bosilganda (Finder/Launchpad/Cmd+Tab) oynani qaytaradi.

        LSUIElement ilovasining Dock belgisi yo'q, shuning uchun yashirilgan
        oyna "yo'qolib qolgandek" tuyulardi.
        """

        def __init__(self, target) -> None:  # noqa: ANN001
            super().__init__()
            self.target = target

        def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
            if (
                event.type() == QEvent.Type.ApplicationActivate
                and self.target is not None
                and not self.target.isVisible()
            ):
                self.target._show_window()
            return False

    # Tarjima ishlayotganda oyna yopilsa ilova menyu panelida yashaydi.
    app.setQuitOnLastWindowClosed(False)
    window = TranslatorWindow()
    activate_filter = _ActivateFilter(window)
    app.installEventFilter(activate_filter)
    window.show()
    if auto_start:
        QTimer.singleShot(1_200, window.start_translator)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui())
