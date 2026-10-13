import unittest

from source_health import update_health, SOURCE_NAMES


class SourceHealthTests(unittest.TestCase):
    def test_repeated_zero_and_recovery(self):
        state = {}
        zero = {name: 1 for name in SOURCE_NAMES}
        zero["Flippa"] = 0
        zero["Acquire"] = 0
        self.assertEqual(update_health(zero, state, "2026-10-12T00:00:00Z"), [])
        self.assertEqual(state["Flippa"]["status"], "unverified_zero")
        alerts = update_health(zero, state, "2026-10-12T01:00:00Z")
        self.assertEqual(len(alerts), 2)
        self.assertEqual(state["Flippa"]["status"], "suspected_failure")
        self.assertEqual(update_health(zero, state, "2026-10-12T02:00:00Z"), [])
        zero["Flippa"] = 6
        alerts = update_health(zero, state, "2026-10-12T03:00:00Z")
        self.assertEqual(len(alerts), 1)
        self.assertIn("recovered", alerts[0])
        self.assertEqual(state["Flippa"]["consecutive_zeros"], 0)

    def test_all_zero_is_not_healthy(self):
        state = {}
        update_health({}, state, "2026-10-12T00:00:00Z")
        self.assertTrue(all(s["status"] == "unverified_zero" for s in state.values()))


if __name__ == "__main__":
    unittest.main()
