import tempfile
import unittest
from pathlib import Path
from deal_pipeline import sync, set_status, add_review, load, save, CHECKLIST

class PipelineTests(unittest.TestCase):
    def test_sync_preserves_decisions_and_history(self):
        history = {"ef:1": {"title": "One", "prices": [{"amount": 120}], "screening": {"acquisition_score": 50}}}
        decisions, n = sync(history, {}, "2026-10-12")
        self.assertEqual(n, 1)
        self.assertEqual(decisions["ef:1"]["status"], "new")
        self.assertEqual(len(decisions["ef:1"]["diligence"]), len(CHECKLIST))
        set_status(decisions, "ef:1", "negotiating", "Seller is open", "2026-10-12")
        add_review(decisions, "ef:1", "NEGOTIATE", "Revenue requires Stripe", "broker_reported", "2026-10-12")
        decisions["ef:1"]["diligence"]["stripe_exports"] = True
        updated = {"ef:1": {"title": "One renamed", "prices": [{"amount": 90}], "screening": {"acquisition_score": 75}}, "ef:2": {"title": "Two"}}
        decisions, n = sync(updated, decisions, "2026-10-13")
        self.assertEqual(n, 1)
        self.assertEqual(decisions["ef:1"]["status"], "negotiating")
        self.assertEqual(len(decisions["ef:1"]["review_notes"]), 2)
        self.assertTrue(decisions["ef:1"]["diligence"]["stripe_exports"])
        self.assertEqual(decisions["ef:1"]["listing"]["latest_price"], 90)
        self.assertEqual(decisions["ef:2"]["status"], "new")

    def test_invalid_transitions_rejected(self):
        d, _ = sync({"id": {}}, {})
        for id_, status, note in [("missing", "passed", "reason"), ("id", "nonsense", "reason"), ("id", "passed", "")]:
            with self.assertRaises(ValueError):
                set_status(d, id_, status, note)
        with self.assertRaises(ValueError):
            add_review(d, "id", "BUY", "notes", "invented_grade")

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "pipeline.json")
            d, _ = sync({"id": {"title": "Test"}}, {})
            save(path, d)
            self.assertEqual(load(path), d)
