"""Native system-audio routing helpers.

The macOS implementation uses the public CoreAudio AudioObject API, so the
packaged app does not depend on Homebrew utilities such as SwitchAudioSource.
"""

from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


@dataclass(frozen=True)
class OutputDevice:
    device_id: int
    name: str


@dataclass(frozen=True)
class InputDevice:
    device_id: int
    name: str


if platform.system() == "Darwin":
    _core_audio = ctypes.CDLL(
        "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
    )
    _core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )

    class _PropertyAddress(ctypes.Structure):
        _fields_ = [
            ("selector", ctypes.c_uint32),
            ("scope", ctypes.c_uint32),
            ("element", ctypes.c_uint32),
        ]

    _core_audio.AudioObjectGetPropertyDataSize.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(_PropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    _core_audio.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
    _core_audio.AudioObjectGetPropertyData.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(_PropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    _core_audio.AudioObjectGetPropertyData.restype = ctypes.c_int32
    _core_audio.AudioObjectSetPropertyData.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(_PropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    _core_audio.AudioObjectSetPropertyData.restype = ctypes.c_int32
    _core_foundation.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    _core_foundation.CFStringGetCString.restype = ctypes.c_bool

    _SYSTEM_OBJECT = 1
    _GLOBAL = _fourcc("glob")
    _MAIN = 0
    _DEVICES = _fourcc("dev#")
    _DEFAULT_OUTPUT = _fourcc("dOut")
    _DEFAULT_INPUT = _fourcc("dIn ")
    _NAME = _fourcc("lnam")
    _UTF8 = 0x08000100


def _address(selector: int) -> "_PropertyAddress":
    return _PropertyAddress(selector, _GLOBAL, _MAIN)


def _check(status: int, operation: str) -> None:
    if status:
        raise RuntimeError(f"CoreAudio {operation} bajarilmadi (OSStatus {status}).")


def _device_ids() -> list[int]:
    address = _address(_DEVICES)
    size = ctypes.c_uint32()
    _check(
        _core_audio.AudioObjectGetPropertyDataSize(
            _SYSTEM_OBJECT, ctypes.byref(address), 0, None, ctypes.byref(size)
        ),
        "qurilmalarni o‘qish",
    )
    count = size.value // ctypes.sizeof(ctypes.c_uint32)
    values = (ctypes.c_uint32 * count)()
    _check(
        _core_audio.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            values,
        ),
        "qurilmalarni o‘qish",
    )
    return [int(value) for value in values]


def _device_name(device_id: int) -> str:
    address = _address(_NAME)
    value = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(value))
    _check(
        _core_audio.AudioObjectGetPropertyData(
            device_id,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        ),
        "qurilma nomini o‘qish",
    )
    buffer = ctypes.create_string_buffer(1024)
    if not value.value or not _core_foundation.CFStringGetCString(
        value, buffer, len(buffer), _UTF8
    ):
        return f"Audio device {device_id}"
    return buffer.value.decode("utf-8", "replace")


def default_output() -> OutputDevice:
    if platform.system() != "Darwin":
        raise RuntimeError("System output’ni avtomatik almashtirish hozir macOS uchun tayyor.")
    address = _address(_DEFAULT_OUTPUT)
    value = ctypes.c_uint32()
    size = ctypes.c_uint32(ctypes.sizeof(value))
    _check(
        _core_audio.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        ),
        "default output’ni o‘qish",
    )
    return OutputDevice(int(value.value), _device_name(int(value.value)))


def default_input() -> InputDevice:
    if platform.system() != "Darwin":
        raise RuntimeError("System input’ni avtomatik almashtirish hozir macOS uchun tayyor.")
    address = _address(_DEFAULT_INPUT)
    value = ctypes.c_uint32()
    size = ctypes.c_uint32(ctypes.sizeof(value))
    _check(
        _core_audio.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(value),
        ),
        "default input’ni o‘qish",
    )
    return InputDevice(int(value.value), _device_name(int(value.value)))


def set_default_output(device: OutputDevice) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("System output’ni avtomatik almashtirish hozir macOS uchun tayyor.")
    address = _address(_DEFAULT_OUTPUT)
    value = ctypes.c_uint32(device.device_id)
    _check(
        _core_audio.AudioObjectSetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.sizeof(value),
            ctypes.byref(value),
        ),
        "default output’ni almashtirish",
    )


def set_default_input(device: InputDevice) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("System input’ni avtomatik almashtirish hozir macOS uchun tayyor.")
    address = _address(_DEFAULT_INPUT)
    value = ctypes.c_uint32(device.device_id)
    _check(
        _core_audio.AudioObjectSetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.sizeof(value),
            ctypes.byref(value),
        ),
        "default input’ni almashtirish",
    )


def route_output_to(device_name: str) -> OutputDevice:
    """Route system audio to ``device_name`` and return the previous output."""

    previous = default_output()
    matches = [
        OutputDevice(device_id, _device_name(device_id))
        for device_id in _device_ids()
        if device_name.casefold() in _device_name(device_id).casefold()
    ]
    if not matches:
        raise RuntimeError(f"System output {device_name!r} topilmadi.")
    set_default_output(matches[0])
    return previous


def route_input_to(device_name: str) -> InputDevice:
    """Route the default system input to ``device_name`` and return the previous input."""

    previous = default_input()
    matches = [
        InputDevice(device_id, _device_name(device_id))
        for device_id in _device_ids()
        if device_name.casefold() in _device_name(device_id).casefold()
    ]
    if not matches:
        raise RuntimeError(f"System input {device_name!r} topilmadi.")
    set_default_input(matches[0])
    return previous
