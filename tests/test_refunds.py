import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.clock import FIXED_NOW, shop_now
from citywheels.cases import calculate_refund

# Spec totals at 2026-06-10 09:00 Europe/Warsaw. The fee is not included.
SAMPLES = {
    "CW-1001": {
        "refund_eur": 90,
        "rental_refund_eur": 40,
        "deposit_refund_eur": 50,
        "rental_refund_percent": 100,
        "channel": "card",
        "can_cancel": True,
        "band": "48h_or_more",
    },
    "CW-1002": {
        "refund_eur": 340,
        "rental_refund_eur": 40,
        "deposit_refund_eur": 300,
        "rental_refund_percent": 50,
        "channel": "card",
        "can_cancel": True,
        "band": "24h_up_to_48h",
    },
    "CW-1003": {
        "refund_eur": 50,
        "rental_refund_eur": 0,
        "deposit_refund_eur": 50,
        "rental_refund_percent": 0,
        "channel": "card",
        "can_cancel": True,
        "band": "under_24h",
    },
    "CW-1004": {
        "refund_eur": 73,
        "rental_refund_eur": 23,
        "deposit_refund_eur": 50,
        "rental_refund_percent": 100,
        "channel": "voucher",
        "can_cancel": True,
        "band": "48h_or_more",
    },
    "CW-1005": {"refund_eur": 0, "can_cancel": False, "channel": None, "status": "cancelled"},
    "CW-1006": {"refund_eur": 0, "can_cancel": False, "channel": None, "status": "completed"},
    "CW-1007": {
        "refund_eur": 186,
        "rental_refund_eur": 36,
        "deposit_refund_eur": 150,
        "rental_refund_percent": 100,
        "channel": "card",
        "can_cancel": True,
        "band": "48h_or_more",
    },
    "CW-1008": {"refund_eur": 0, "can_cancel": False, "channel": None, "status": "in_progress"},
    "CW-1009": {
        "refund_eur": 169,
        "rental_refund_eur": 119,
        "deposit_refund_eur": 50,
        "rental_refund_percent": 100,
        "channel": "card",
        "can_cancel": True,
        "band": "48h_or_more",
    },
    "CW-1010": {
        "refund_eur": 170,
        "rental_refund_eur": 20,
        "deposit_refund_eur": 150,
        "rental_refund_percent": 50,
        "channel": "card",
        "can_cancel": True,
        "band": "24h_up_to_48h",
    },
}


def _write_booking(directory: Path, booking: dict) -> None:
    (directory / "bookings.json").write_text(json.dumps([booking]), encoding="utf-8")


def _booking(start, **overrides) -> dict:
    booking = {
        "id": "CW-TEST",
        "status": "confirmed",
        "bike_type": "city",
        "bikes": 1,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=2)).isoformat(),
        "add_ons": [],
        "rental_price_eur": 10,
        "booking_fee_eur": 2,
        "deposit_eur": 1,
        "paid_with": "card",
    }
    booking.update(overrides)
    return booking


class RefundTests(unittest.TestCase):
    def test_sample_totals_use_the_fixed_clock(self):
        for booking_id, expected in SAMPLES.items():
            with self.subTest(booking_id=booking_id):
                result = calculate_refund(booking_id)
                for key, value in expected.items():
                    self.assertEqual(result[key], value, key)
                self.assertEqual(result["booking_fee_refund_eur"], 0)
                self.assertEqual(
                    result["refund_eur"],
                    result["rental_refund_eur"] + result["deposit_refund_eur"],
                )
                if result["can_cancel"] and result["booking_fee_eur"]:
                    with_fee = (
                        result["rental_refund_eur"]
                        + result["deposit_refund_eur"]
                        + result["booking_fee_eur"]
                    )
                    self.assertNotEqual(result["refund_eur"], with_fee)
                if result["channel"] == "card":
                    self.assertIn("same card", result["channel_detail"])
                if result["channel"] == "voucher":
                    self.assertIn("voucher", result["channel_detail"])
                    self.assertIn("never as cash", result["channel_detail"])
                if not result["can_cancel"]:
                    self.assertEqual(result["deposit_refund_eur"], 0)
                    self.assertEqual(result["rental_refund_eur"], 0)

    def test_cw_1002_is_340_not_342(self):
        result = calculate_refund("CW-1002")
        self.assertEqual(result["refund_eur"], 340)
        self.assertNotEqual(result["refund_eur"], 342)
        self.assertEqual(result["booking_fee_eur"], 2)

    def test_results_do_not_read_the_wall_clock(self):
        package = ROOT / "citywheels"
        for path in package.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\.now\s*\(")
            self.assertNotRegex(text, r"\.today\s*\(")
            self.assertNotIn("time.time(", text)
        self.assertEqual(shop_now().isoformat(), "2026-06-10T09:00:00+02:00")
        seen = []

        def spy():
            seen.append("shop_now")
            return FIXED_NOW

        with patch("citywheels.refunds.shop_now", spy):
            self.assertEqual(calculate_refund("CW-1002")["refund_eur"], 340)
            self.assertEqual(calculate_refund("CW-1007")["refund_eur"], 186)
        self.assertEqual(seen, ["shop_now", "shop_now"])

    def test_explicit_now_changes_the_band(self):
        at_fixed = calculate_refund("CW-1007")
        one_second_later = calculate_refund("CW-1007", now=FIXED_NOW + timedelta(seconds=1))
        self.assertEqual(at_fixed["rental_refund_percent"], 100)
        self.assertEqual(at_fixed["refund_eur"], 186)
        self.assertEqual(one_second_later["rental_refund_percent"], 50)
        self.assertEqual(one_second_later["refund_eur"], 168)

    def test_constructed_boundaries_and_no_reprice(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            under_24 = FIXED_NOW + timedelta(hours=24) - timedelta(seconds=1)
            under_48 = FIXED_NOW + timedelta(hours=48) - timedelta(seconds=1)
            exact_24 = FIXED_NOW + timedelta(hours=24)
            exact_48 = FIXED_NOW + timedelta(hours=48)
            cases = [
                ("under-24", under_24, 0, 1, "under_24h"),
                ("under-48", under_48, 50, 6, "24h_up_to_48h"),
                ("exact-24", exact_24, 50, 6, "24h_up_to_48h"),
                ("exact-48", exact_48, 100, 11, "48h_or_more"),
            ]
            for name, start, percent, total, band in cases:
                with self.subTest(name=name):
                    _write_booking(directory, _booking(start, id="CW-TEST"))
                    result = calculate_refund("CW-TEST", data_dir=directory, now=FIXED_NOW)
                    self.assertEqual(result["rental_refund_percent"], percent)
                    self.assertEqual(result["band"], band)
                    self.assertEqual(result["refund_eur"], total)
                    self.assertEqual(result["rental_price_eur"], 10)
                    self.assertEqual(result["booking_fee_refund_eur"], 0)
                    self.assertNotEqual(result["refund_eur"], total + 2)

            odd = _booking(
                FIXED_NOW + timedelta(days=5),
                id="CW-ODD",
                rental_price_eur=7,
                deposit_eur=3,
                booking_fee_eur=2,
            )
            _write_booking(directory, odd)
            odd_result = calculate_refund("CW-ODD", data_dir=directory)
            self.assertEqual(odd_result["rental_price_eur"], 7)
            self.assertEqual(odd_result["rental_refund_eur"], 7)
            self.assertEqual(odd_result["refund_eur"], 10)

    def test_each_non_cancellable_status_refunds_nothing_even_far_ahead(self):
        start = FIXED_NOW + timedelta(days=10)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for status in ("in_progress", "completed", "cancelled"):
                with self.subTest(status=status):
                    _write_booking(
                        directory,
                        _booking(
                            start,
                            id="CW-LOCK",
                            status=status,
                            rental_price_eur=100,
                            deposit_eur=80,
                        ),
                    )
                    result = calculate_refund("CW-LOCK", data_dir=directory)
                    self.assertFalse(result["can_cancel"])
                    self.assertEqual(result["refund_eur"], 0)
                    self.assertEqual(result["deposit_refund_eur"], 0)
                    self.assertEqual(result["rental_refund_eur"], 0)

    def test_missing_booking_does_not_crash(self):
        result = calculate_refund("CW-NO-SUCH")
        self.assertFalse(result["found"])
        self.assertIsNone(result["refund_eur"])
        self.assertIn("not found", result["message"].lower())

    def test_voucher_channel_on_a_constructed_booking(self):
        start = FIXED_NOW + timedelta(days=3)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_booking(directory, _booking(start, id="CW-V", paid_with="voucher"))
            result = calculate_refund("CW-V", data_dir=directory)
            self.assertEqual(result["channel"], "voucher")
            self.assertIn("never as cash", result["channel_detail"])
            self.assertEqual(result["refund_eur"], 11)


if __name__ == "__main__":
    unittest.main()
