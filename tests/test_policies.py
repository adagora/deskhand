import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.policies import search_policies

DATA = ROOT / "data"
SUMMER_HOURS = "08:00\u201320:00"
EBIKE_DAY = "40 EUR"
AGE_LINE = "Electric bikes: from 16 years old."
CANCEL_LINE = "48 hours or more before the start"


def _blob(result: dict) -> str:
    return "\n".join(passage["text"] for passage in result["passages"])


def _assert_verbatim(test: unittest.TestCase, result: dict) -> None:
    test.assertGreater(len(result["passages"]), 0)
    for passage in result["passages"]:
        source = (DATA / "policies" / passage["source"]).read_text(encoding="utf-8")
        test.assertIn(passage["text"], source)
        test.assertIn(passage["heading"], passage["text"])


class PolicySearchTests(unittest.TestCase):
    def test_summer_opening_line_is_verbatim(self):
        faq = (DATA / "policies" / "faq.md").read_text(encoding="utf-8")
        self.assertIn(SUMMER_HOURS, faq)
        result = search_policies("What time do you open on Saturday?")
        _assert_verbatim(self, result)
        self.assertIn(SUMMER_HOURS, _blob(result))

    def test_ebike_day_price_is_verbatim(self):
        prices = (DATA / "policies" / "prices.md").read_text(encoding="utf-8")
        self.assertIn(EBIKE_DAY, prices)
        self.assertIn("e-bike", prices)
        result = search_policies("How much is an e-bike for a whole day?")
        _assert_verbatim(self, result)
        blob = _blob(result)
        self.assertIn(EBIKE_DAY, blob)
        self.assertIn("e-bike", blob)

    def test_ebike_age_minimum_is_verbatim(self):
        faq = (DATA / "policies" / "faq.md").read_text(encoding="utf-8")
        self.assertIn(AGE_LINE, faq)
        self.assertIn("16", AGE_LINE)
        result = search_policies("Can my 14-year-old rent an e-bike?")
        _assert_verbatim(self, result)
        self.assertIn(AGE_LINE, _blob(result))

    def test_cancellation_table_is_verbatim(self):
        rules = (DATA / "policies" / "cancellations.md").read_text(encoding="utf-8")
        for line in (
            CANCEL_LINE,
            "Less than 48 hours, but 24 hours or more before the start",
            "Less than 24 hours before the start",
            "100%",
            "50%",
            "0%",
            "The booking fee is never refunded.",
        ):
            self.assertIn(line, rules)
        result = search_policies("What are the cancellation refund percentages?")
        _assert_verbatim(self, result)
        blob = _blob(result)
        self.assertIn(CANCEL_LINE, blob)
        self.assertIn("100%", blob)
        self.assertIn("50%", blob)
        self.assertIn("0%", blob)

    def test_empty_query_returns_no_passages(self):
        result = search_policies("   ")
        self.assertEqual(result["passages"], [])

    def test_shop_location_passage(self):
        result = search_policies("Where are you?")
        _assert_verbatim(self, result)
        self.assertIn("Riverside Park", _blob(result))


if __name__ == "__main__":
    unittest.main()
