import unittest

from audio_routing import AudioEndpoint, DuplexRoutes, validate_duplex_routes


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


if __name__ == "__main__":
    unittest.main()
