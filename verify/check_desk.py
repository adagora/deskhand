"""Every status and refund band, checked against the real functions.

The bands are the same three the TLA+ model calls far, mid, and near.
far is 48 hours out, mid is 24 hours out, near is one second under 24 hours.
"""

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.clock import FIXED_NOW
from citywheels.refunds import refund_for_booking
from citywheels.schedule import change_allowed

STATUSES = ("confirmed", "in_progress", "completed", "cancelled")
BANDS = {
    "far": timedelta(hours=48),
    "mid": timedelta(hours=24),
    "near": timedelta(hours=24) - timedelta(seconds=1),
}


def booking(status: str, start) -> dict:
    return {
        "id": "CW-MODEL",
        "status": status,
        "bike_type": "e-bike",
        "bikes": 1,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=2)).isoformat(),
        "add_ons": [],
        "rental_price_eur": 80,
        "booking_fee_eur": 2,
        "deposit_eur": 300,
        "paid_with": "card",
    }


def expect_refund(status: str, band: str) -> tuple[int, int]:
    if status != "confirmed":
        return 0, 0
    if band == "far":
        return 80, 380
    if band == "mid":
        return 40, 340
    return 0, 300


def main() -> int:
    for status in STATUSES:
        for band, delta in BANDS.items():
            start = FIXED_NOW + delta
            result = refund_for_booking(booking(status, start), now=FIXED_NOW)
            rental, total = expect_refund(status, band)
            allowed = change_allowed(status=status, start=start, now=FIXED_NOW)
            if result["rental_refund_eur"] != rental or result["refund_eur"] != total:
                print(
                    f"refund {status} {band}: got {result['refund_eur']}, "
                    f"expected {total} with the 2 EUR fee left out"
                )
                return 1
            if result["refund_eur"] == total + result["booking_fee_eur"] and result["booking_fee_eur"]:
                print(f"refund {status} {band} includes the booking fee")
                return 1
            if allowed != (status == "confirmed" and band != "near"):
                print(f"change {status} {band}: allowed={allowed}")
                return 1
    print(f"ok {len(STATUSES) * len(BANDS)} status and band pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
