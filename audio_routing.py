"""Pure audio-route validation shared by the desktop UI and tests."""

from __future__ import annotations

from dataclasses import dataclass


VIRTUAL_MARKERS = (
    "blackhole",
    "cable input",
    "cable output",
    "vb-audio virtual cable",
    "cable-a",
    "cable-b",
)


@dataclass(frozen=True)
class AudioEndpoint:
    index: int
    name: str


@dataclass(frozen=True)
class DuplexRoutes:
    incoming_input: AudioEndpoint
    incoming_output: AudioEndpoint
    outgoing_input: AudioEndpoint
    outgoing_output: AudioEndpoint


def is_virtual_device(name: str) -> bool:
    folded = name.casefold()
    return any(marker in folded for marker in VIRTUAL_MARKERS)


def virtual_device_family(name: str) -> str:
    """Return the logical cable, not its separate Windows input/output endpoint."""

    folded = " ".join(name.casefold().split())
    if "blackhole" in folded:
        return folded
    if "cable-a" in folded:
        return "vb-cable-a"
    if "cable-b" in folded:
        return "vb-cable-b"
    if "cable input" in folded or "cable output" in folded or "vb-audio virtual cable" in folded:
        return "vb-cable"
    return folded


def validate_duplex_routes(routes: DuplexRoutes) -> None:
    """Reject unsafe full-duplex routes before CoreAudio is changed."""

    if not is_virtual_device(routes.incoming_input.name):
        raise ValueError("Meeting input uchun virtual audio qurilma tanlang.")
    if is_virtual_device(routes.incoming_output.name):
        raise ValueError("Meeting tarjimasi uchun fizik speaker/headphone tanlang.")
    if is_virtual_device(routes.outgoing_input.name):
        raise ValueError("Zoom’ga gapirish uchun fizik mikrofon tanlang.")
    if not is_virtual_device(routes.outgoing_output.name):
        raise ValueError("Zoom output uchun ikkinchi virtual audio qurilma tanlang.")
    if (
        routes.incoming_input.index == routes.outgoing_output.index
        or virtual_device_family(routes.incoming_input.name)
        == virtual_device_family(routes.outgoing_output.name)
    ):
        raise ValueError(
            "IKKALASI rejimiga ikkita alohida virtual audio qurilma kerak: "
            "BlackHole 2ch va BlackHole 16ch."
        )
