from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from casework.model import CustomerScope, Observation, Proposal, Refusal, named
from casework.store import reading, transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS booking (
  tenant TEXT NOT NULL, id TEXT NOT NULL, customer TEXT NOT NULL, version INTEGER NOT NULL,
  status TEXT NOT NULL, starts_at TEXT NOT NULL, rental_minor INTEGER NOT NULL,
  deposit_minor INTEGER NOT NULL, booking_fee_minor INTEGER NOT NULL, currency TEXT NOT NULL,
  payment_ref TEXT NOT NULL, payment_kind TEXT NOT NULL, cancelled_by TEXT,
  PRIMARY KEY (tenant, id)
);
CREATE TABLE IF NOT EXISTS refund (
  operation_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, booking_id TEXT NOT NULL,
  amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, payment_ref TEXT NOT NULL,
  payment_kind TEXT NOT NULL, settled_at TEXT NOT NULL
);
"""
_SHOP_ZONE = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True)
class Booking:
    id: str
    tenant: str
    customer: str
    version: int
    status: str
    starts_at: str
    rental_minor: int
    deposit_minor: int
    booking_fee_minor: int
    currency: str
    payment_ref: str
    payment_kind: str
    cancelled_by: str | None = None


class BookingBackend(Protocol):
    def owned(self, scope: CustomerScope, booking_id: str) -> Booking: ...
    def cancel(self, proposal: Proposal) -> None: ...
    def refund(self, proposal: Proposal, settled_at: str) -> None: ...
    def observe(self, proposal: Proposal) -> Observation: ...


def _minor(euros) -> int:
    return int((Decimal(str(euros)) * 100).to_integral_value())


def booking_from_shop_record(record: dict, tenant: str, customer: str) -> Booking:
    start = datetime.fromisoformat(record["start"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=_SHOP_ZONE)
    return Booking(
        id=record["id"],
        tenant=tenant,
        customer=customer,
        version=1,
        status=record["status"],
        starts_at=start.isoformat(),
        rental_minor=_minor(record["rental_price_eur"]),
        deposit_minor=_minor(record["deposit_eur"]),
        booking_fee_minor=_minor(record["booking_fee_eur"]),
        currency="EUR",
        payment_ref=f"original-{record['id']}",
        payment_kind=record["paid_with"],
    )


def _booking(row) -> Booking:
    return Booking(**{key: row[key] for key in row.keys()})


class SqliteBackend:
    def __init__(self, path: Path):
        self.path = Path(path)
        with transaction(self.path) as connection:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)

    def seed(self, bookings: list[Booking]) -> None:
        with transaction(self.path) as connection:
            for booking in bookings:
                connection.execute(
                    "INSERT OR IGNORE INTO booking (tenant, id, customer, version, status, starts_at, rental_minor,"
                    " deposit_minor, booking_fee_minor, currency, payment_ref, payment_kind, cancelled_by)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        booking.tenant, booking.id, booking.customer, booking.version, booking.status,
                        booking.starts_at, booking.rental_minor, booking.deposit_minor, booking.booking_fee_minor,
                        booking.currency, booking.payment_ref, booking.payment_kind, booking.cancelled_by,
                    ),
                )

    def owned(self, scope: CustomerScope, booking_id: str) -> Booking:
        if not named(scope.tenant) or not named(scope.customer) or not named(booking_id):
            raise Refusal("invalid_scope")
        with reading(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM booking WHERE tenant=? AND customer=? AND id=?",
                (scope.tenant, scope.customer, booking_id),
            ).fetchone()
        if row is None:
            raise Refusal("booking_not_accessible")
        return _booking(row)

    def cancel(self, proposal: Proposal) -> None:
        with transaction(self.path) as connection:
            row = self._proposed_booking(connection, proposal)
            if row["cancelled_by"] == proposal.id and row["status"] == "cancelled":
                return
            if row["version"] != proposal.booking_version or row["status"] != "confirmed":
                raise Refusal("booking_changed")
            connection.execute(
                "UPDATE booking SET status='cancelled', cancelled_by=?, version=version+1 WHERE tenant=? AND id=?",
                (proposal.id, proposal.tenant, proposal.booking_id),
            )

    def refund(self, proposal: Proposal, settled_at: str) -> None:
        with transaction(self.path) as connection:
            row = self._proposed_booking(connection, proposal)
            if row["cancelled_by"] != proposal.id or row["status"] != "cancelled":
                raise Refusal("cancellation_not_observed")
            previous = connection.execute(
                "SELECT * FROM refund WHERE operation_id=?", (proposal.id,)
            ).fetchone()
            if previous is not None:
                if (
                    previous["amount_minor"] != proposal.amount_minor
                    or previous["currency"] != proposal.currency
                    or previous["payment_ref"] != row["payment_ref"]
                    or previous["payment_kind"] != proposal.payment_kind
                ):
                    raise Refusal("idempotency_conflict")
                return
            if row["currency"] != proposal.currency or row["payment_kind"] != proposal.payment_kind:
                raise Refusal("refund_method_changed")
            connection.execute(
                "INSERT INTO refund (operation_id, tenant, booking_id, amount_minor, currency, payment_ref,"
                " payment_kind, settled_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    proposal.id, proposal.tenant, proposal.booking_id, proposal.amount_minor, proposal.currency,
                    row["payment_ref"], row["payment_kind"], settled_at,
                ),
            )

    def observe(self, proposal: Proposal) -> Observation:
        with reading(self.path) as connection:
            row = self._proposed_booking(connection, proposal)
            refund = connection.execute(
                "SELECT * FROM refund WHERE operation_id=?", (proposal.id,)
            ).fetchone()
        cancelled_here = row["cancelled_by"] == proposal.id and row["status"] == "cancelled"
        facts = {"booking_status": row["status"], "booking_version": row["version"]}
        if refund is None:
            if cancelled_here:
                return Observation(state="cancelled-only", **facts)
            changed = row["status"] != "confirmed" or row["version"] != proposal.booking_version
            return Observation(state="changed" if changed else "none", **facts)
        if (
            not cancelled_here
            or refund["amount_minor"] != proposal.amount_minor
            or refund["currency"] != proposal.currency
            or refund["payment_ref"] != row["payment_ref"]
            or refund["booking_id"] != row["id"]
            or refund["payment_kind"] != proposal.payment_kind
        ):
            return Observation(state="mismatch", code="readback_mismatch", **facts)
        return Observation(
            state="settled",
            amount_minor=refund["amount_minor"],
            currency=refund["currency"],
            payment_kind=refund["payment_kind"],
            **facts,
        )

    def settled_refund(self, booking: Booking) -> Observation | None:
        with reading(self.path) as connection:
            refund = connection.execute(
                "SELECT * FROM refund WHERE operation_id=? AND tenant=? AND booking_id=?",
                (booking.cancelled_by, booking.tenant, booking.id),
            ).fetchone()
        if refund is None:
            return None
        return Observation(
            state="settled",
            booking_status=booking.status,
            booking_version=booking.version,
            amount_minor=refund["amount_minor"],
            currency=refund["currency"],
            payment_kind=refund["payment_kind"],
        )

    def refund_count(self) -> int:
        with reading(self.path) as connection:
            return connection.execute("SELECT COUNT(*) FROM refund").fetchone()[0]

    def refunds(self) -> list[dict]:
        with reading(self.path) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM refund ORDER BY settled_at")]

    def _proposed_booking(self, connection, proposal: Proposal):
        row = connection.execute(
            "SELECT * FROM booking WHERE tenant=? AND customer=? AND id=?",
            (proposal.tenant, proposal.customer, proposal.booking_id),
        ).fetchone()
        if row is None:
            raise Refusal("booking_not_accessible")
        return row
