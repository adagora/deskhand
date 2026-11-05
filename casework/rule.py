import hashlib
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from casework.backend import Booking
from citywheels.refunds import refund_for_booking

_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_FILES = ("citywheels/refunds.py", "data/policies/cancellations.md")


def _euros(minor: int) -> Decimal:
    return Decimal(minor) / Decimal(100)


def _shop_record(booking: Booking) -> dict:
    return {
        "id": booking.id,
        "status": booking.status,
        "start": booking.starts_at,
        "rental_price_eur": _euros(booking.rental_minor),
        "booking_fee_eur": _euros(booking.booking_fee_minor),
        "deposit_eur": _euros(booking.deposit_minor),
        "paid_with": booking.payment_kind,
    }


def refund_minor(booking: Booking, business_at: datetime) -> int:
    quote = refund_for_booking(_shop_record(booking), now=business_at)
    return int((Decimal(str(quote["refund_eur"])) * 100).to_integral_value())


def original_refund_minor(booking: Booking, business_at: datetime) -> int:
    return refund_minor(replace(booking, status="confirmed"), business_at)


def current_release(root: Path = _ROOT) -> str:
    identity = hashlib.sha256()
    for name in _RELEASE_FILES:
        identity.update(name.encode())
        identity.update((root / name).read_bytes().replace(b"\r\n", b"\n"))
    return identity.hexdigest()
