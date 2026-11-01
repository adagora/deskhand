"""Cancellation refunds. The amount is computed here, never by the model.

Uses the stored rental_price_eur. Does not look up the price table again.
Exactly 48 hours before the start is a full rental refund. Exactly 24 hours
is half. The booking fee is never included.
"""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from citywheels.clock import WARSAW, shop_now

_REQUIRED = (
    "id",
    "status",
    "start",
    "rental_price_eur",
    "booking_fee_eur",
    "deposit_eur",
    "paid_with",
)

_FEE_NOTE = "The booking fee is not refunded."


def _money(value) -> Decimal:
    return Decimal(str(value))


def json_money(value: Decimal):
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return int(quantized)
    return float(quantized)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=WARSAW)
    return moment


def _channel(paid_with: str) -> tuple[str | None, str]:
    if paid_with == "card":
        return "card", "Refunded to the same card within 5 working days."
    if paid_with == "voucher":
        return (
            "voucher",
            "Refunded as a new voucher of the same value, never as cash. "
            "The deposit is refunded as a voucher too.",
        )
    return (
        None,
        "Contact the staff about how this refund is paid. "
        f"It was not paid by card or voucher (paid_with={paid_with}).",
    )


def refund_for_booking(booking: dict, now: datetime | None = None) -> dict:
    missing = [field for field in _REQUIRED if field not in booking]
    if missing:
        return {
            "found": True,
            "id": booking.get("id"),
            "error": "Booking is missing fields: " + ", ".join(missing),
            "refund_eur": None,
        }
    moment = shop_now() if now is None else _aware(now)
    rental = _money(booking["rental_price_eur"])
    fee = _money(booking["booking_fee_eur"])
    deposit = _money(booking["deposit_eur"])
    status = booking["status"]
    result = {
        "found": True,
        "id": booking["id"],
        "status": status,
        "rental_price_eur": json_money(rental),
        "booking_fee_eur": json_money(fee),
        "booking_fee_refund_eur": 0,
        "deposit_eur": json_money(deposit),
        "paid_with": booking["paid_with"],
        "booking_fee_note": _FEE_NOTE,
    }
    if status != "confirmed":
        result.update(
            {
                "can_cancel": False,
                "band": "not_cancellable",
                "rental_refund_percent": 0,
                "rental_refund_eur": 0,
                "deposit_refund_eur": 0,
                "refund_eur": 0,
                "channel": None,
                "channel_detail": None,
                "reason": (
                    f"A booking with status {status} cannot be cancelled, "
                    "and nothing is refunded."
                ),
            }
        )
        return result

    start = datetime.fromisoformat(str(booking["start"]))
    if start.tzinfo is None:
        start = start.replace(tzinfo=WARSAW)
    delta = start - moment
    # Exactly 48h is included in the full refund. Exactly 24h is included in the half refund.
    if delta >= timedelta(hours=48):
        percent = 100
        band = "48h_or_more"
    elif delta >= timedelta(hours=24):
        percent = 50
        band = "24h_up_to_48h"
    else:
        percent = 0
        band = "under_24h"
    rental_refund = (rental * Decimal(percent) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = (rental_refund + deposit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    channel, detail = _channel(booking["paid_with"])
    result.update(
        {
            "can_cancel": True,
            "band": band,
            "rental_refund_percent": percent,
            "rental_refund_eur": json_money(rental_refund),
            "deposit_refund_eur": json_money(deposit),
            "refund_eur": json_money(total),
            "channel": channel,
            "channel_detail": detail,
        }
    )
    return result
