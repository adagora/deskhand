import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.clock import FIXED_NOW
from citywheels.refunds import refund_for_booking
from citywheels.schedule import change_allowed

BANDS = {
    "far": timedelta(hours=48),
    "mid": timedelta(hours=24),
    "near": timedelta(hours=24) - timedelta(seconds=1),
}

QUOTE = {
    ("confirmed", "far"): ("full", 100, True),
    ("confirmed", "mid"): ("half", 50, True),
    ("confirmed", "near"): ("deposit", 0, False),
    ("in_progress", "far"): ("nothing", 0, False),
    ("in_progress", "mid"): ("nothing", 0, False),
    ("in_progress", "near"): ("nothing", 0, False),
    ("completed", "far"): ("nothing", 0, False),
    ("completed", "mid"): ("nothing", 0, False),
    ("completed", "near"): ("nothing", 0, False),
    ("cancelled", "far"): ("nothing", 0, False),
    ("cancelled", "mid"): ("nothing", 0, False),
    ("cancelled", "near"): ("nothing", 0, False),
}


def booking(status, start):
    return {
        "id": "CW-TABLE",
        "status": status,
        "start": start.isoformat(),
        "rental_price_eur": 80,
        "booking_fee_eur": 2,
        "deposit_eur": 300,
        "paid_with": "card",
    }


class DecisionTableTest(unittest.TestCase):
    def test_every_status_and_band_matches_the_desk_model(self):
        for (status, band), (quote, percent, change) in QUOTE.items():
            with self.subTest(status=status, band=band):
                start = FIXED_NOW + BANDS[band]
                result = refund_for_booking(booking(status, start), now=FIXED_NOW)
                self.assertEqual(result["rental_refund_percent"], percent)
                deposit = 0 if quote == "nothing" else 300
                self.assertEqual(result["deposit_refund_eur"], deposit)
                self.assertEqual(result["refund_eur"], 80 * percent // 100 + deposit)
                self.assertEqual(result["booking_fee_refund_eur"], 0)
                self.assertEqual(
                    change_allowed(status=status, start=start, now=FIXED_NOW), change
                )


if __name__ == "__main__":
    unittest.main()
