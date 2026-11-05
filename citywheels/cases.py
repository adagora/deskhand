"""Load one booking, then ask the refund rule. The file read stays here."""

from datetime import datetime
from pathlib import Path

from citywheels.bookings import get_booking
from citywheels.clock import WARSAW, shop_now
from citywheels.paths import default_data_dir
from citywheels.refunds import refund_for_booking


def calculate_refund(
    booking_id: str,
    data_dir: Path | None = None,
    now: datetime | None = None,
    at: datetime | None = None,
) -> dict:
    """Refund for cancelling one booking at `now`, or the fixed shop clock.

    `at` answers a what-if question about a later time. It cannot be earlier than now.
    """
    looked_up = get_booking(booking_id, data_dir=data_dir or default_data_dir())
    if not looked_up["found"]:
        return {
            "found": False,
            "id": looked_up.get("id", ""),
            "message": looked_up.get("message", "Booking was not found."),
            "refund_eur": None,
        }
    if at is None:
        return refund_for_booking(looked_up["booking"], now=now)
    current = shop_now() if now is None else now
    moment = at if at.tzinfo else at.replace(tzinfo=WARSAW)
    if moment < (current if current.tzinfo else current.replace(tzinfo=WARSAW)):
        return {
            "found": True,
            "id": looked_up["booking"]["id"],
            "error": "The time for a what-if quote cannot be earlier than now.",
            "refund_eur": None,
        }
    quote = refund_for_booking(looked_up["booking"], now=moment)
    if "error" not in quote:
        quote["as_of"] = moment.isoformat()
    return quote


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
