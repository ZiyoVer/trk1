import unittest

from product_app import is_engine_connected_line, is_expected_engine_exit


class ProductStatusTests(unittest.TestCase):
    def test_edcom_connection_log_is_recognized_case_insensitively(self) -> None:
        self.assertTrue(
            is_engine_connected_line(
                "✓ Ulandi. EDCOM gateway tayyor. EN nutqini kutyapman..."
            )
        )
        self.assertTrue(is_engine_connected_line("✓ EDCOM’ga ulandi."))
        self.assertFalse(is_engine_connected_line("EDCOM serveriga ulanmoqda..."))

    def test_user_stop_is_not_reported_as_a_crash(self) -> None:
        self.assertTrue(is_expected_engine_exit(-15, stop_requested=True))
        self.assertTrue(is_expected_engine_exit(0, stop_requested=False))
        self.assertFalse(is_expected_engine_exit(-15, stop_requested=False))


if __name__ == "__main__":
    unittest.main()
