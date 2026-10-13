import unittest
import tempfile
from pathlib import Path
from acquisition_intelligence import evaluate, update_history, save_history, load_history, build_intelligence_digest, classify_deal, build_actionable_digest


class IntelligenceTests(unittest.TestCase):
    def test_reddit_noise_suppressed(self):
        for title in ("Anti Scroll - September Update (first sale!!)",
                      "[FOR SALE] YouTube Channel - Not monetized",
                      "I'm selling my first mobile app to someone. How do I even go about it?"):
            self.assertEqual(classify_deal({"source": "reddit/AppBusiness", "title": title}), "irrelevant")
        self.assertEqual(classify_deal({"source": "reddit/saasforsale", "title": "Selling my saas"}), "lead")

    def test_concise_digest(self):
        hits = [
            {"source": "reddit/AppBusiness", "title": "Anti Scroll - September Update (first sale!!)", "url": "https://example.com/1"},
            {"source": "reddit/saasforsale", "title": "Selling my saas", "url": "https://example.com/2"},
            {"source": "empireflippers", "title": "B2B SaaS", "profit_monthly": 3000, "price": 160000, "url": "https://example.com/3"},
        ]
        body = build_actionable_digest(hits, [], {"Flippa": 0, "Empire Flippers": 6})
        self.assertNotIn("Anti Scroll", body)
        self.assertIn("Selling my saas", body)
        self.assertIn("CHECK COLLECTORS", body)
        self.assertIn("$54,000-$90,000", body)
        self.assertIn("UNDERWRITE / NEGOTIATE", body)

    def test_good_deal_math(self):
        d = {"id":"flippa:1","source":"flippa","title":"B2B SaaS","price":36000,"profit_monthly":3000,"business_age_months":30,"verified_revenue":True}
        v = evaluate(d)
        self.assertEqual(v["profit_multiple"],1)
        self.assertEqual(v["payback_months"],12)
        self.assertEqual(v["annual_cash_yield"],1)
        self.assertEqual(v["target_offer_low"],54000)
        self.assertEqual(v["target_offer_high"],90000)
        self.assertGreaterEqual(v["acquisition_score"],70)

    def test_high_ask_can_be_negotiated(self):
        v = evaluate({"source":"flippa","title":"B2B SaaS","profit_monthly":3000,"price":180000})
        self.assertTrue(v["negotiation_candidate"])
        self.assertEqual(v["target_offer_high"],90000)
        self.assertEqual(v["discount_to_target_pct"],50)

    def test_unknowns_not_invented(self):
        v = evaluate({"source":"reddit/SaaS","title":"Selling a SaaS","price":0})
        self.assertIsNone(v["profit_multiple"])
        self.assertEqual(v["confidence"],"low")

    def test_operator_signals(self):
        v = evaluate({"source":"reddit/SaaS","title":"Stuck at $5k MRR; churn; looking for growth partner"})
        self.assertGreaterEqual(v["operator_score"],45)

    def test_price_history(self):
        history = {}
        deal = {"id":"flippa:1","title":"Tool","price":50000,"url":"https://example.com"}
        self.assertEqual(update_history([deal],history,"2026-10-10"),[])
        self.assertEqual(update_history([deal],history,"2026-10-11"),[])
        deal["price"]=40000
        dropped = update_history([deal],history,"2026-10-12")
        self.assertEqual(len(dropped),1)
        self.assertEqual(dropped[0]["discount_pct"],20)
        self.assertEqual(len(history["flippa:1"]["prices"]),2)
        self.assertIn("PRICE REDUCTIONS",build_intelligence_digest([],dropped))
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"history.json"
            save_history(history,p)
            self.assertEqual(load_history(p),history)

if __name__ == "__main__":
    unittest.main()
