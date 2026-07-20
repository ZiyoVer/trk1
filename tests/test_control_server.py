import sqlite3
import tempfile
import unittest
from pathlib import Path

from control_server import ControlError, ControlStore, TokenSigner
from licensing import LicenseError, validate_control_url


class ControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.secret = "s" * 48
        self.store = ControlStore(Path(self.temp.name) / "control.sqlite3", self.secret)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_user_activation_usage_and_revoke(self) -> None:
        created = self.store.create_user("Pilot One", "pilot@example.com", max_devices=1)
        activated = self.store.activate(
            created["license_key"], "device-1", "MacBook", "Darwin arm64", "0.3.0"
        )
        self.assertEqual(activated["user_id"], created["id"])

        session_id = self.store.start_session(
            created["id"],
            "device-1",
            "en",
            "MacBook Microphone",
            "BlackHole 2ch",
            "uz",
            "outgoing",
        )
        stats = self.store.stats()
        self.assertEqual(stats["active_users"], 1)
        self.assertEqual(stats["online_devices"], 1)
        self.assertEqual(stats["live_sessions"], 1)
        self.assertEqual(self.store.list_users()[0]["live_sessions"], 1)
        live_session = self.store.list_live_sessions()[0]
        self.assertEqual(live_session["source_language"], "uz")
        self.assertEqual(live_session["target_language"], "en")
        self.assertEqual(live_session["mode"], "outgoing")

        self.store.end_session(created["id"], session_id)
        self.assertEqual(self.store.stats()["live_sessions"], 0)
        self.store.set_user_status(created["id"], "revoked")
        with self.assertRaises(ControlError) as context:
            self.store.heartbeat(created["id"], "device-1", "0.3.0")
        self.assertEqual(context.exception.status, 403)

    def test_device_limit_is_enforced(self) -> None:
        created = self.store.create_user("Pilot", "limit@example.com", max_devices=1)
        self.store.activate(created["license_key"], "one", "One", "macOS", "0.3.0")
        with self.assertRaises(ControlError):
            self.store.activate(created["license_key"], "two", "Two", "Windows", "0.3.0")

    def test_signed_token_rejects_tampering(self) -> None:
        signer = TokenSigner(self.secret)
        token = signer.issue("user", "device")
        self.assertEqual(signer.verify(token)["uid"], "user")
        with self.assertRaises(ControlError):
            signer.verify(token + "x")

    def test_session_rejects_invalid_or_identical_languages(self) -> None:
        created = self.store.create_user("Pilot", "languages@example.com")
        self.store.activate(created["license_key"], "device", "Mac", "macOS", "0.4.0")
        with self.assertRaises(ControlError):
            self.store.start_session(
                created["id"], "device", "uz", "Microphone", "Speaker", "uz"
            )
        with self.assertRaises(ControlError):
            self.store.start_session(
                created["id"], "device", "de", "Microphone", "Speaker", "auto"
            )

    def test_old_session_schema_is_migrated_without_data_reset(self) -> None:
        database = Path(self.temp.name) / "control.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("DROP TABLE sessions")
            db.execute(
                """CREATE TABLE sessions (
                       id TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                       device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                       target_language TEXT NOT NULL,
                       input_device TEXT NOT NULL,
                       output_device TEXT NOT NULL,
                       started_at TEXT NOT NULL,
                       last_seen_at TEXT NOT NULL,
                       ended_at TEXT
                   )"""
            )
        ControlStore(database, self.secret)
        with sqlite3.connect(database) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        self.assertIn("source_language", columns)
        self.assertIn("mode", columns)


class LicenseUrlTests(unittest.TestCase):
    def test_remote_control_requires_https(self) -> None:
        self.assertEqual(validate_control_url("http://127.0.0.1:8787/"), "http://127.0.0.1:8787")
        self.assertEqual(validate_control_url("https://control.example.com/"), "https://control.example.com")
        with self.assertRaises(LicenseError):
            validate_control_url("http://control.example.com")


if __name__ == "__main__":
    unittest.main()
