import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.bookings import get_booking

DATA = ROOT / "data"


class BookingLookupTests(unittest.TestCase):
    def test_cw_1002_matches_the_file(self):
        stored = json.loads((DATA / "bookings.json").read_text(encoding="utf-8"))
        expected = next(booking for booking in stored if booking["id"] == "CW-1002")
        result = get_booking("CW-1002")
        self.assertTrue(result["found"])
        self.assertEqual(result["booking"], expected)
        self.assertEqual(set(result["booking"]), set(expected))

    def test_lookup_is_case_insensitive(self):
        result = get_booking(" cw-1002 ")
        self.assertTrue(result["found"])
        self.assertEqual(result["booking"]["id"], "CW-1002")

    def test_unknown_id_is_not_found_without_raising(self):
        result = get_booking("CW-9999")
        self.assertFalse(result["found"])
        self.assertEqual(result["id"], "CW-9999")
        self.assertIn("not found", result["message"].lower())
        self.assertNotIn("booking", result)

    def test_empty_id_is_not_found_without_raising(self):
        result = get_booking("")
        self.assertFalse(result["found"])
        self.assertIn("not found", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
