import unittest
from bs4 import BeautifulSoup
from saas_scout import normalize_ef
from acquisition_intelligence import evaluate


class EmpireFlippersUnitTests(unittest.TestCase):
    def test_annual_values_are_converted_to_monthly(self):
        html = """
        <div class="listing-item">
          <span class="listing-number">#88253</span>
          <div class="metric-item price"><span class="value">$239,214</span></div>
          <div class="metric-item profit"><span class="value">$72,489</span></div>
          <div class="metric-item revenue"><span class="value">$74,814</span></div>
          <div class="metric-item monetization"><span class="value">SaaS</span></div>
          <div class="niches">News & Education, Travel</div>
        </div>
        """
        card = BeautifulSoup(html, "html.parser").select_one(".listing-item")
        deal = normalize_ef(card)
        self.assertAlmostEqual(deal["profit_monthly"], 72489 / 12)
        self.assertAlmostEqual(deal["revenue_monthly"], 74814 / 12)
        self.assertAlmostEqual(deal["multiple"], 239214 / 72489)
        intelligence = evaluate(deal)
        self.assertEqual(intelligence["target_offer_low"], round(72489 * 1.5))
        self.assertEqual(intelligence["target_offer_high"], round(72489 * 2.5))
        self.assertAlmostEqual(intelligence["payback_months"], 239214 / (72489 / 12))


if __name__ == "__main__":
    unittest.main()
