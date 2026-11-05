"""Load one booking, then ask the refund rule. The file read stays here."""

from datetime import datetime
from pathlib import Path

from citywheels.bookings import get_booking
from citywheels.paths import default_data_dir
from citywheels.refunds import refund_for_booking


def calculate_refund(
    booking_id: str,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Refund for cancelling one booking at `now`, or the fixed shop clock."""
    looked_up = get_booking(booking_id, data_dir=data_dir or default_data_dir())
    if not looked_up["found"]:
        return {
            "found": False,
            "id": looked_up.get("id", ""),
            "message": looked_up.get("message", "Booking was not found."),
            "refund_eur": None,
        }
    return refund_for_booking(looked_up["booking"], now=now)


def request_cancellation(booking_id: str, data_dir: Path | None = None) -> dict:
    """Name the customer's request for the case workflow. This changes nothing."""
    looked_up = get_booking(booking_id, data_dir=data_dir or default_data_dir())
    if not looked_up["found"]:
        return {
            "found": False,
            "id": looked_up.get("id", ""),
            "message": looked_up.get("message", "Booking was not found."),
        }
    return {
        "found": True,
        "write_intent": {"action": "cancel-refund", "booking_id": looked_up["booking"]["id"]},
        "message": (
            "Nothing has changed yet. The customer gets an exact quote and must confirm it "
            "before the booking is cancelled."
        ),
    }
