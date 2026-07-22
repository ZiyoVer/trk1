"""Pure audio-route validation shared by the desktop UI and tests."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Windows bir xil VB-Audio drayverini bir necha marta o'rnatganda ikkinchi
# nusxani "2- VB-Audio Hi-Fi Cable" deb nomlaydi. Endpoint nomida bu raqam
# qavs ichida turadi: "Hi-Fi Cable Output (2- VB-Audio ...)". Shu raqamni
# ajratib olamiz — nusxalarni ALOHIDA kabel deb hisoblash uchun.
_INSTANCE_RE = re.compile(r"(\d+)-\s*vb-audio")


def _cable_instance(folded: str) -> str:
    match = _INSTANCE_RE.search(folded)
    return f"-{match.group(1)}" if match else ""


VIRTUAL_MARKERS = (
    "blackhole",
    "cable input",
    "cable output",
    "vb-audio virtual cable",
    "cable-a",
    "cable-b",
    # VB-Audio Hi-Fi Cable (duplex'ning ikkinchi kabeli). Uning ikkala
    # tomoni ham shu marker'ga tushadi: "Hi-Fi Cable Output" (yozib
    # olish) va "Speakers/Динамики (VB-Audio Hi-Fi Cable)" (ijro).
    "hi-fi cable",
    "vb-audio hi-fi",
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
    # Hi-Fi Cable — asosiy VB-CABLE'dan ALOHIDA oila. "cable output"
    # tekshiruvidan OLDIN qaraladi, aks holda uning nomi ("Hi-Fi Cable
    # Output") "vb-cable"ga qo'shilib ketardi (duplex ikkalasini bir xil
    # deb hisoblab rad qilardi). Nusxa raqami (2-, 3-) oxiriga qo'shiladi,
    # shunda "VB-Audio Hi-Fi Cable" va "2- VB-Audio Hi-Fi Cable" turli
    # kabel deb qaraladi — ikkalasi rejimi ular bilan ishlashi uchun shart.
    if "hi-fi" in folded or "hifi" in folded:
        return "vb-hifi-cable" + _cable_instance(folded)
    if "cable-a" in folded:
        return "vb-cable-a"
    if "cable-b" in folded:
        return "vb-cable-b"
    if "cable input" in folded or "cable output" in folded or "vb-audio virtual cable" in folded:
        return "vb-cable" + _cable_instance(folded)
    return folded


def is_forbidden_route(
    input_name: str, output_name: str, input_id: int, output_id: int
) -> bool:
    """Bitta virtual kabelning ikki uchini input+output qilish = feedback loop.

    macOS'da BlackHole 2ch bitta qurilma (bir xil index); Windows'da esa
    "CABLE Input" va "CABLE Output" ALOHIDA indexli endpointlar, lekin bitta
    kabel — shuning uchun index emas, virtual_device_family solishtiriladi.
    """
    if not (is_virtual_device(input_name) and is_virtual_device(output_name)):
        return False
    if input_id == output_id:
        return True
    return virtual_device_family(input_name) == virtual_device_family(output_name)


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
