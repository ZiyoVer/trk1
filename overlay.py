"""Tiny translucent macOS controller for the meeting translator."""

from __future__ import annotations

import atexit
import fcntl
import os
import queue
import signal
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
LOCK_PATH = Path("/tmp/english-uzbek-translator.lock")
BG = "#101827"
FG = "#F8FAFC"
MUTED = "#94A3B8"
GREEN = "#22C55E"
GREEN_ACTIVE = "#16A34A"
RED = "#EF4444"
RED_ACTIVE = "#DC2626"
AMBER = "#F59E0B"


class TranslatorOverlay:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("English to Uzbek Translator")
        self.root.geometry(self._initial_geometry(520, 266))
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg=BG)

        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.drag_x = 0
        self.drag_y = 0
        self.closing = False
        self.source_caption = ""
        self.target_caption = ""
        self.last_source_at = 0.0
        self.last_target_at = 0.0
        self.captions_cleared = True

        self._build_ui()
        self._bind_dragging()
        atexit.register(self._cleanup_child)
        signal.signal(signal.SIGTERM, self._signal_close)
        signal.signal(signal.SIGINT, self._signal_close)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._handle_events)

    def _initial_geometry(self, width: int, height: int) -> str:
        self.root.update_idletasks()
        x = max(20, self.root.winfo_screenwidth() - width - 28)
        return f"{width}x{height}+{x}+48"

    def _build_ui(self) -> None:
        # Canvas is intentional: macOS Aqua can ignore tk.Button background
        # colors, producing white-on-white controls. Canvas colors are exact.
        self.canvas = tk.Canvas(
            self.root,
            width=520,
            height=266,
            bg=BG,
            borderwidth=0,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            16,
            22,
            text="LIVE  →  O‘ZBEKCHA",
            anchor="w",
            fill=FG,
            font=("Helvetica Neue", 15, "bold"),
        )
        self.canvas.create_text(
            194,
            22,
            text="CHARON",
            anchor="w",
            fill=MUTED,
            font=("Helvetica Neue", 9, "bold"),
        )
        self.canvas.create_text(
            499,
            21,
            text="×",
            fill=FG,
            font=("Helvetica Neue", 19, "bold"),
            tags=("close_click",),
        )
        self.status_dot = self.canvas.create_text(
            17,
            52,
            text="●",
            anchor="w",
            fill=MUTED,
            font=("Helvetica Neue", 10),
        )
        self.status_label = self.canvas.create_text(
            35,
            52,
            text="TAYYOR",
            anchor="w",
            fill=MUTED,
            font=("Helvetica Neue", 10, "bold"),
        )

        self.canvas.create_line(15, 70, 505, 70, fill="#334155", width=1)
        self.source_language_label = self.canvas.create_text(
            16,
            84,
            text="ORIGINAL",
            anchor="w",
            fill=MUTED,
            font=("Helvetica Neue", 9, "bold"),
        )
        self.source_subtitle = self.canvas.create_text(
            16,
            101,
            text="Gap kutilmoqda…",
            anchor="nw",
            width=488,
            fill=FG,
            font=("Helvetica Neue", 12, "bold"),
        )
        self.canvas.create_text(
            16,
            151,
            text="O‘ZBEKCHA",
            anchor="w",
            fill=GREEN,
            font=("Helvetica Neue", 9, "bold"),
        )
        self.target_subtitle = self.canvas.create_text(
            16,
            168,
            text="Tarjima shu yerda chiqadi…",
            anchor="nw",
            width=488,
            fill="#DCFCE7",
            font=("Helvetica Neue", 12, "bold"),
        )

        self.start_bg = self.canvas.create_rectangle(
            15, 216, 250, 252, fill=GREEN, outline="", tags=("start_click",)
        )
        self.start_text = self.canvas.create_text(
            132,
            234,
            text="▶  BOSHLASH",
            fill="white",
            font=("Helvetica Neue", 10, "bold"),
            tags=("start_click",),
        )
        self.stop_bg = self.canvas.create_rectangle(
            270, 216, 505, 252, fill="#334155", outline="", tags=("stop_click",)
        )
        self.stop_text = self.canvas.create_text(
            387,
            234,
            text="■  TO‘XTATISH",
            fill="#94A3B8",
            font=("Helvetica Neue", 10, "bold"),
            tags=("stop_click",),
        )
        self.start_enabled = True
        self.stop_enabled = False
        self.canvas.tag_bind("start_click", "<Button-1>", self._start_clicked)
        self.canvas.tag_bind("stop_click", "<Button-1>", self._stop_clicked)
        self.canvas.tag_bind("close_click", "<Button-1>", lambda _event: self.close())

    def _start_clicked(self, _event: tk.Event) -> None:
        if self.start_enabled:
            self.start()

    def _stop_clicked(self, _event: tk.Event) -> None:
        if self.stop_enabled:
            self.stop()

    def _set_controls(self, *, start: bool, stop: bool) -> None:
        self.start_enabled = start
        self.stop_enabled = stop
        self.canvas.itemconfigure(self.start_bg, fill=GREEN if start else "#334155")
        self.canvas.itemconfigure(self.start_text, fill="white" if start else "#94A3B8")
        self.canvas.itemconfigure(self.stop_bg, fill=RED if stop else "#334155")
        self.canvas.itemconfigure(self.stop_text, fill="white" if stop else "#94A3B8")

    def _bind_dragging(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._drag_start, add="+")
        self.canvas.bind("<B1-Motion>", self._drag_move, add="+")

    def _drag_start(self, event: tk.Event) -> None:
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event: tk.Event) -> None:
        self.root.geometry(
            f"+{event.x_root - self.drag_x}+{event.y_root - self.drag_y}"
        )

    def _set_status(self, text: str, color: str) -> None:
        self.canvas.itemconfigure(self.status_label, text=text, fill=color)
        self.canvas.itemconfigure(self.status_dot, fill=color)

    @staticmethod
    def _append_caption(current: str, chunk: str, last_at: float) -> tuple[str, bool]:
        now = time.monotonic()
        new_turn = (
            not current
            or now - last_at > 2.0
            or current.rstrip().endswith((".", "?", "!"))
        )
        combined = chunk if new_turn else f"{current} {chunk}"
        if len(combined) > 140:
            combined = "…" + combined[-139:].lstrip()
        return combined, new_turn

    def _update_source_caption(self, language: str, text: str) -> None:
        caption, new_turn = self._append_caption(
            self.source_caption, text, self.last_source_at
        )
        self.source_caption = caption
        self.last_source_at = time.monotonic()
        self.captions_cleared = False
        self.canvas.itemconfigure(
            self.source_language_label, text=f"ORIGINAL  ·  {language.upper()}"
        )
        self.canvas.itemconfigure(self.source_subtitle, text=caption, fill=FG)
        if new_turn:
            self.target_caption = ""
            self.canvas.itemconfigure(
                self.target_subtitle, text="Tarjima qilinmoqda…", fill=MUTED
            )

    def _update_target_caption(self, text: str) -> None:
        caption, _ = self._append_caption(
            self.target_caption, text, self.last_target_at
        )
        self.target_caption = caption
        self.last_target_at = time.monotonic()
        self.captions_cleared = False
        self.canvas.itemconfigure(
            self.target_subtitle, text=caption, fill="#DCFCE7"
        )

    def _clear_stale_captions(self) -> None:
        latest = max(self.last_source_at, self.last_target_at)
        if self.captions_cleared or not latest or time.monotonic() - latest < 12:
            return
        self.source_caption = ""
        self.target_caption = ""
        self.captions_cleared = True
        self.canvas.itemconfigure(
            self.source_language_label, text="ORIGINAL", fill=MUTED
        )
        self.canvas.itemconfigure(
            self.source_subtitle, text="Gap kutilmoqda…", fill=MUTED
        )
        self.canvas.itemconfigure(
            self.target_subtitle, text="Tarjima shu yerda chiqadi…", fill=MUTED
        )

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self._set_status("ULANMOQDA…", AMBER)
        self._set_controls(start=False, stop=True)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.process = subprocess.Popen(
                [str(PROJECT_DIR / "run.sh"), "--voice", "Charon"],
                cwd=PROJECT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
        except Exception as error:
            self.process = None
            self._set_status(f"XATO: {error}", RED)
            self._set_controls(start=True, stop=False)
            return
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        last_line = ""
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            last_line = line
            if "Ulandi." in line:
                self.events.put(("status", "ISHLAYAPTI"))
            elif "qayta ulanadi" in line:
                self.events.put(("status", "QAYTA ULANMOQDA"))
            elif " › " in line:
                language, text = line.split(" › ", 1)
                normalized = language.replace("-", "").replace("_", "")
                if normalized.isalpha() and 2 <= len(language) <= 12 and text:
                    if language.upper().startswith("UZ"):
                        self.events.put(("target", text))
                    else:
                        self.events.put(("source", f"{language}\t{text}"))
        return_code = process.wait()
        detail = last_line if return_code else ""
        self.events.put(("exit", detail))

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self._set_status("TO‘XTATILMOQDA…", AMBER)
        self._set_controls(start=False, stop=False)
        try:
            os.killpg(self.process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass

    def _handle_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status" and value == "ISHLAYAPTI":
                    self._set_status("TARJIMA ISHLAYAPTI", GREEN)
                elif kind == "status":
                    self._set_status(value, AMBER)
                elif kind == "source":
                    language, text = value.split("\t", 1)
                    self._update_source_caption(language, text)
                elif kind == "target":
                    self._update_target_caption(value)
                elif kind == "exit":
                    self.process = None
                    self._set_controls(start=True, stop=False)
                    if self.closing:
                        self.root.destroy()
                        return
                    if value and not value.startswith("To‘xtadi"):
                        self._set_status("XATO — QAYTA BOSING", RED)
                    else:
                        self._set_status("TO‘XTADI", MUTED)
        except queue.Empty:
            pass
        self._clear_stale_captions()
        self.root.after(100, self._handle_events)

    def close(self) -> None:
        self.closing = True
        if self.process and self.process.poll() is None:
            self.stop()
            self.root.after(3000, self._force_close)
        else:
            self.root.destroy()

    def _signal_close(self, _signum, _frame) -> None:  # noqa: ANN001
        self.root.after(0, self.close)

    def _cleanup_child(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _force_close(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    TranslatorOverlay().run()
