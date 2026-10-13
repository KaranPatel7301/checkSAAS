import unittest

from acquisition_intelligence import classify_deal, build_actionable_digest
from saas_scout import passes_ef, CRITERIA


class FlexibleAcquisitionTests(unittest.TestCase):
    def test_low_profit_is_turnaround_not_pass(self):
        deal = {"source": "empireflippers", "id": "ef:turnaround",
                "title": "B2B SaaS", "summary": "subscription",
                "profit_monthly": 1100, "price": 60000,
                "multiple": 60000 / (1100 * 12), "url": "https://example.com"}
        self.assertTrue(passes_ef(deal))
        self.assertEqual(classify_deal(deal), "turnaround")
        digest = build_actionable_digest([deal], [], {})
        self.assertIn("TURNAROUND WATCHLIST", digest)
        self.assertIn("https://example.com", digest)

    def test_high_ask_can_be_negotiated(self):
        deal = {"source": "empireflippers", "title": "B2B SaaS",
                "summary": "subscription", "profit_monthly": 4000,
                "price": 320000, "multiple": 320000 / 48000}
        self.assertTrue(passes_ef(deal))

    def test_reject_purely_structurally_bad_keyword(self):
        deal = {"source": "empireflippers", "title": "AI girlfriend",
                "summary": "subscription", "profit_monthly": 4000,
                "price": 320000, "multiple": 320000 / 48000}
        self.assertFalse(passes_ef(deal))


if __name__ == "__main__":
    unittest.main()
