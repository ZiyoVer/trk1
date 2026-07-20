import unittest

from licensing import LicenseClient


class FakeLicenseClient(LicenseClient):
    def __init__(self) -> None:
        super().__init__("https://control.example.com", "key", "device", "test")
        self.access_token = "token"
        self.started = 0
        self.ended: list[str] = []

    def _request(self, path, payload, *, authenticated=False):  # noqa: ANN001
        if path.endswith("/start"):
            self.started += 1
            return {"session_id": f"session-{self.started}"}
        if path.endswith("/end"):
            self.ended.append(payload["session_id"])
            return {}
        return {}


class LicenseClientTests(unittest.TestCase):
    def test_duplex_can_track_and_end_both_admin_sessions(self) -> None:
        client = FakeLicenseClient()
        client.start_session("uz", "BlackHole 2ch", "Speakers", mode="incoming")
        client.start_session("en", "Microphone", "BlackHole 16ch", mode="outgoing")
        self.assertEqual(client.session_ids, ["session-1", "session-2"])
        client.end_session()
        self.assertEqual(client.ended, ["session-1", "session-2"])
        self.assertEqual(client.session_ids, [])


if __name__ == "__main__":
    unittest.main()
