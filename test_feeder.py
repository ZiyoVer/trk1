"""Feed a short macOS English TTS sample directly into BlackHole for E2E testing."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import wave
from pathlib import Path

import sounddevice as sd

from audio import PCMConverter, auto_input_device


DEFAULT_TEXT = (
    "Hello. This is a real time English to Uzbek translation test. "
    "The meeting starts tomorrow morning at nine o'clock."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send English TTS into BlackHole")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="English text to synthesize")
    parser.add_argument("--rate", type=int, default=165, help="Speech rate in words per minute")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    blackhole = auto_input_device(None)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "english.wav"
        subprocess.run(
            [
                "say",
                "-v",
                "Samantha",
                "-r",
                str(args.rate),
                "-o",
                str(path),
                "--file-format=WAVE",
                "--data-format=LEI16@16000",
                "--channels=1",
                args.text,
            ],
            check=True,
        )
        with wave.open(str(path), "rb") as audio_file:
            rate = audio_file.getframerate()
            channels = audio_file.getnchannels()
            width = audio_file.getsampwidth()
            data = audio_file.readframes(audio_file.getnframes())

    if width != 2:
        raise RuntimeError(f"Kutilmagan sample width: {width}")
    converter = PCMConverter(rate, blackhole.sample_rate, channels, blackhole.channels)
    converted = converter.convert(data)
    with sd.RawOutputStream(
        device=blackhole.index,
        samplerate=blackhole.sample_rate,
        channels=blackhole.channels,
        dtype="int16",
    ) as stream:
        stream.write(converted)
    print("English test audio BlackHole’ga yuborildi.")


if __name__ == "__main__":
    main()
