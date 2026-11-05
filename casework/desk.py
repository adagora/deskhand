from datetime import datetime, timedelta, timezone
from typing import Callable

from casework.backend import Booking, BookingBackend
from casework.grants import GrantBook
from casework.model import (
    CANCEL_REFUND,
    Consent,
    CustomerScope,
    Observation,
    Principal,
    Proposal,
    Refusal,
    Review,
    consent_binds,
    evidence_digest,
    is_rejected,
    named,
    review_binds,
)
from casework.rule import current_release, original_refund_minor, refund_minor
from citywheels.clock import shop_now

PAYMENT_KINDS = ("card", "voucher")


def system_now() -> datetime:
    return datetime.now(timezone.utc)


class Desk:
    def __init__(
        self,
        backend: BookingBackend,
        grants: GrantBook,
        *,
        release: Callable[[], str] = current_release,
        clock: Callable[[], datetime] = system_now,
        business_clock: Callable[[], datetime] = shop_now,
        auto_approve_minor: int = 10000,
        quote_ttl: timedelta = timedelta(days=1),
    ):
        self.backend = backend
        self.grants = grants
        self.release = release
        self.clock = clock
        self.business_clock = business_clock
        self.auto_approve_minor = auto_approve_minor
        self.quote_ttl = quote_ttl

    def propose(
        self,
        scope: CustomerScope,
        case_id: str,
        booking_id: str,
        intent: str = "citywheels.cancellation",
        storm_claim: bool = False,
        correlation: str = "",
    ) -> Proposal:
        if not named(case_id) or not named(intent):
            raise Refusal("invalid_proposal")
        booking = self.backend.owned(scope, booking_id)
        if booking.status != "confirmed":
            raise Refusal("not_cancellable")
        if storm_claim:
            raise Refusal("staff_required_for_claim")
        if booking.payment_kind not in PAYMENT_KINDS:
            raise Refusal("staff_required_for_payment_route")
        now, business_at = self.clock(), self.business_clock()
        amount = refund_minor(booking, business_at)
        identity = {
            "tenant": booking.tenant,
            "customer": booking.customer,
            "case_id": case_id,
            "booking_id": booking.id,
            "booking_version": booking.version,
            "intent": intent,
            "action": CANCEL_REFUND,
            "amount_minor": amount,
            "currency": booking.currency,
            "payment_kind": booking.payment_kind,
            "release": self.release(),
            "review_required": amount > self.auto_approve_minor,
        }
        return Proposal(
            id=evidence_digest(identity),
            business_at=business_at.isoformat(),
            created_at=now.isoformat(),
            expires_at=(now + self.quote_ttl).isoformat(),
            correlation=correlation,
            **identity,
        )

    def recheck(self, proposal: Proposal, consent: Consent | None, review: Review | None) -> str:
        self._require_authority(proposal, consent, review)
        now = self.clock()
        if now < datetime.fromisoformat(proposal.created_at):
            raise Refusal("clock_regressed")
        if now >= datetime.fromisoformat(proposal.expires_at):
            raise Refusal("proposal_expired")
        scope = CustomerScope(proposal.tenant, proposal.customer)
        booking = self.backend.owned(scope, proposal.booking_id)
        if booking.status != "confirmed" or booking.version != proposal.booking_version:
            raise Refusal("booking_changed")
        if booking.currency != proposal.currency or booking.payment_kind != proposal.payment_kind:
            raise Refusal("refund_method_changed")
        if refund_minor(booking, self.business_clock()) != proposal.amount_minor:
            raise Refusal("amount_changed")
        return self._transact_grant(proposal, now)

    def recheck_resolution(
        self,
        principal: Principal,
        proposal: Proposal,
        consent: Consent | None,
        review: Review | None,
    ) -> str:
        now = self.clock()
        if (
            principal.tenant != proposal.tenant
            or not named(principal.id)
            or "operator" not in principal.roles
            or "reviewer" not in principal.roles
        ):
            raise Refusal("resolution_forbidden")
        observed = self.backend.observe(proposal)
        if observed.state == "mismatch":
            raise Refusal("readback_mismatch")
        if observed.state != "cancelled-only":
            raise Refusal("no_committed_cancellation")
        self._require_authority(proposal, consent, review)
        scope = CustomerScope(proposal.tenant, proposal.customer)
        booking = self.backend.owned(scope, proposal.booking_id)
        if (
            booking.cancelled_by != proposal.id
            or booking.version != proposal.booking_version + 1
            or booking.currency != proposal.currency
            or booking.payment_kind != proposal.payment_kind
        ):
            raise Refusal("booking_changed")
        original_at = datetime.fromisoformat(proposal.business_at)
        if original_refund_minor(booking, original_at) != proposal.amount_minor:
            raise Refusal("original_facts_unverified")
        return self._transact_grant(proposal, now)

    def cancel(self, proposal: Proposal) -> None:
        self.backend.cancel(proposal)

    def refund(self, proposal: Proposal) -> None:
        self.backend.refund(proposal, self.clock().isoformat())

    def observe(self, proposal: Proposal) -> Observation:
        return self.backend.observe(proposal)

    def settled_for(self, scope: CustomerScope, booking_id: str) -> Observation | None:
        booking: Booking = self.backend.owned(scope, booking_id)
        if booking.status != "cancelled" or not booking.cancelled_by:
            return None
        return self.backend.settled_refund(booking)

    def _require_authority(self, proposal: Proposal, consent: Consent | None, review: Review | None) -> None:
        if not consent_binds(consent, proposal):
            raise Refusal("consent_required")
        if is_rejected(proposal, review):
            raise Refusal("operation_rejected")
        if proposal.review_required and not (review_binds(review, proposal) and review.verdict == "approved"):
            raise Refusal("operation_approval_required")

    def _transact_grant(self, proposal: Proposal, now: datetime) -> str:
        release = self.release()
        if release != proposal.release:
            raise Refusal("policy_changed")
        grant = self.grants.authorize(
            tenant=proposal.tenant,
            intent=proposal.intent,
            action=proposal.action,
            amount_minor=proposal.amount_minor,
            currency=proposal.currency,
            release=release,
            now=now,
        )
        if grant.tier != "transact":
            raise Refusal("tier_cannot_transact")
        return grant.id
