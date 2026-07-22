import unittest

import audio

from audio_routing import (
    AudioEndpoint,
    DuplexRoutes,
    is_forbidden_route,
    validate_duplex_routes,
)


class ForbiddenRouteTests(unittest.TestCase):
    def test_same_vb_cable_endpoints_are_rejected_on_windows(self) -> None:
        # Windows: CABLE Output (input tomoni) va CABLE Input (output tomoni)
        # ALOHIDA indexlar, lekin bitta kabel.
        self.assertTrue(is_forbidden_route("CABLE Output", "CABLE Input", 4, 5))

    def test_same_blackhole_device_is_rejected_on_macos(self) -> None:
        self.assertTrue(is_forbidden_route("BlackHole 2ch", "BlackHole 2ch", 2, 2))

    def test_distinct_cable_families_are_allowed(self) -> None:
        self.assertFalse(is_forbidden_route("CABLE-A Output", "CABLE-B Input", 4, 5))

    def test_virtual_to_physical_route_is_allowed(self) -> None:
        self.assertFalse(is_forbidden_route("BlackHole 2ch", "MacBook Air Speakers", 2, 3))


def valid_routes() -> DuplexRoutes:
    return DuplexRoutes(
        incoming_input=AudioEndpoint(0, "BlackHole 2ch"),
        incoming_output=AudioEndpoint(2, "MacBook Air Speakers"),
        outgoing_input=AudioEndpoint(1, "MacBook Air Microphone"),
        outgoing_output=AudioEndpoint(3, "BlackHole 16ch"),
    )


class DuplexRouteTests(unittest.TestCase):
    def test_two_independent_virtual_devices_are_accepted(self) -> None:
        validate_duplex_routes(valid_routes())

    def test_one_virtual_device_for_both_directions_is_rejected(self) -> None:
        routes = valid_routes()
        unsafe = DuplexRoutes(
            incoming_input=routes.incoming_input,
            incoming_output=routes.incoming_output,
            outgoing_input=routes.outgoing_input,
            outgoing_output=AudioEndpoint(0, "BlackHole 2ch"),
        )
        with self.assertRaisesRegex(ValueError, "ikkita alohida"):
            validate_duplex_routes(unsafe)

    def test_physical_mic_and_speaker_roles_are_enforced(self) -> None:
        routes = valid_routes()
        with self.assertRaisesRegex(ValueError, "fizik mikrofon"):
            validate_duplex_routes(
                DuplexRoutes(
                    routes.incoming_input,
                    routes.incoming_output,
                    AudioEndpoint(3, "BlackHole 16ch"),
                    routes.outgoing_output,
                )
            )

    def test_windows_input_and_output_names_of_same_cable_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ikkita alohida"):
            validate_duplex_routes(
                DuplexRoutes(
                    AudioEndpoint(4, "CABLE Output (VB-Audio Virtual Cable)"),
                    AudioEndpoint(2, "Speakers"),
                    AudioEndpoint(1, "Microphone"),
                    AudioEndpoint(5, "CABLE Input (VB-Audio Virtual Cable)"),
                )
            )




class AliasOutputTests(unittest.TestCase):
    """Windows'dagi 'yo'naltirgich' qurilmalar avtomatik tanlanmasin.

    Jonli Windows logi (2026-07-20): output 'Microsoft Sound Mapper -
    Output' tanlangan; u tizim defaultiga ishora qiladi va meeting ovozi
    uchun default kabelga qo'yilgani sabab tarjima kirish kabeliga
    qaytib, bitta gap cheksiz takrorlangan.
    """

    def test_windows_alias_devices_are_recognized(self) -> None:
        for name in (
            "Microsoft Sound Mapper - Output",
            "Primary Sound Driver",
            "Primary Sound Capture Driver",
            # Ruscha Windows (masofaviy testda topildi)
            "\u041f\u0435\u0440\u0435\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0437\u0432\u0443\u043a\u043e\u0432\u044b\u0445 \u0443\u0441\u0442\u0440. - Output",
            "\u041f\u0435\u0440\u0432\u0438\u0447\u043d\u044b\u0439 \u0437\u0432\u0443\u043a\u043e\u0432\u043e\u0439 \u0434\u0440\u0430\u0439\u0432\u0435\u0440",
        ):
            self.assertTrue(audio.is_alias_output(name), name)

    def test_real_devices_are_not_aliases(self) -> None:
        for name in (
            "Mi Monitor (NVIDIA High Definition Audio)",
            "MacBook Air Speakers",
            "Realtek Digital Output",
        ):
            self.assertFalse(audio.is_alias_output(name), name)

    def test_virtual_cables_are_not_physical_outputs(self) -> None:
        for name in ("CABLE Input (VB-Audio Virtual Cable)", "BlackHole 2ch"):
            self.assertFalse(
                audio.is_physical_output({"name": name, "max_output_channels": 16}), name
            )



class PreferredPhysicalOutputTests(unittest.TestCase):
    """Sessiya davomida qurilma almashishi: TIZIM tanlovi asos bo'ladi."""

    DEVICES = [
        {"name": "MacBook Air Microphone", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "BlackHole 2ch", "max_input_channels": 2, "max_output_channels": 2},
        {"name": "MacBook Air Speakers", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "P2961", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "JBL TUNE 510BT", "max_input_channels": 0, "max_output_channels": 2},
    ]

    def _patch(self, system_default: str) -> None:
        devices = self.DEVICES

        class FakeSD:
            default = type("d", (), {"device": [0, 2]})()

            @staticmethod
            def query_devices(*args, **kwargs):
                return devices

        import system_audio

        self._original = (audio.sd, audio.find_device, system_audio.default_output)
        audio.sd = FakeSD
        audio.find_device = lambda query, kind: audio.DeviceChoice(
            int(query), devices[int(query)]["name"], 48000, 2
        )
        system_audio.default_output = lambda: system_audio.OutputDevice(0, system_default)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        import system_audio

        audio.sd, audio.find_device, system_audio.default_output = self._original

    def test_any_headphone_brand_is_followed(self) -> None:
        # macOS BT naushnik ulanganda uni o'zi tizim chiqishiga qo'yadi.
        self._patch(system_default="JBL TUNE 510BT")
        self.assertEqual(audio.preferred_physical_output().name, "JBL TUNE 510BT")

    def test_connected_monitor_does_not_steal_audio(self) -> None:
        # P2961 regressiyasi: monitor ulangani ovozni tortib ketmasin —
        # tizim chiqishi MacBook karnayida qolsa, shu qoladi.
        self._patch(system_default="MacBook Air Speakers")
        self.assertEqual(audio.preferred_physical_output().name, "MacBook Air Speakers")

    def test_virtual_system_output_means_no_switch(self) -> None:
        # "Tinglash" rejimida tizim chiqishi kabelga qaratilgan — bunda
        # hech narsa almashtirilmaydi (aks holda tarjima kabelga tushardi).
        self._patch(system_default="BlackHole 2ch")
        self.assertIsNone(audio.preferred_physical_output())




class HiFiCableTests(unittest.TestCase):
    """Duplex'ning ikkinchi kabeli — Hi-Fi Cable (real Windows testda topildi).

    Ilova uni asosiy VB-CABLE bilan bir xil oila deb hisoblab, chiqish
    tomonini ("Speakers/Динамики") virtual emas deb tanirdi — natijada
    'kerakli audio qurilma topilmadi'.
    """

    HIFI_NAMES = [
        "Hi-Fi Cable Output (VB-Audio Hi-Fi Cable)",
        "Speakers (VB-Audio Hi-Fi Cable)",
        "Динамики (2- VB-Audio Hi-Fi Cable)",
    ]

    def test_all_hifi_endpoints_are_virtual(self) -> None:
        from audio_routing import is_virtual_device
        for name in self.HIFI_NAMES:
            self.assertTrue(is_virtual_device(name), name)

    def test_hifi_is_distinct_family_from_vb_cable(self) -> None:
        from audio_routing import virtual_device_family
        vb = virtual_device_family("CABLE Output (VB-Audio Virtual Cable)")
        for name in self.HIFI_NAMES:
            self.assertNotEqual(virtual_device_family(name), vb, name)

    def test_duplex_vb_cable_plus_hifi_is_allowed(self) -> None:
        from audio_routing import is_forbidden_route
        self.assertFalse(
            is_forbidden_route(
                "CABLE Output (VB-Audio Virtual Cable)",
                "Speakers (VB-Audio Hi-Fi Cable)",
                2,
                4,
            )
        )

    def test_hifi_playback_not_treated_as_physical(self) -> None:
        import audio
        for name in ("Speakers (VB-Audio Hi-Fi Cable)",
                     "Динамики (2- VB-Audio Hi-Fi Cable)"):
            self.assertFalse(
                audio.is_physical_output({"name": name, "max_output_channels": 8}), name
            )

if __name__ == "__main__":
    unittest.main()
