import unittest
import tempfile
from pathlib import Path
from acquisition_intelligence import evaluate, update_history, save_history, load_history, build_intelligence_digest


class IntelligenceTests(unittest.TestCase):
    def test_good_deal_math(self):
        d = {"id":"flippa:1","source":"flippa","title":"B2B SaaS","price":36000,"profit_monthly":3000,"business_age_months":30,"verified_revenue":True}
        v = evaluate(d)
        self.assertEqual(v["profit_multiple"],1)
        self.assertEqual(v["payback_months"],12)
        self.assertEqual(v["annual_cash_yield"],1)
        self.assertGreaterEqual(v["acquisition_score"],70)

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
