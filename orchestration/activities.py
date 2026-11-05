from temporalio import activity
from temporalio.exceptions import ApplicationError

from casework.desk import Desk
from casework.evidence import NoEvidence
from casework.model import (
    CaseRequest,
    Consent,
    CustomerScope,
    DecisionRef,
    Gate,
    Observation,
    Prepared,
    Principal,
    Proposal,
    Refusal,
    Resolution,
    Review,
)
from casework.staff import StaffQueue


def _stop_retrying(refusal: Refusal) -> ApplicationError:
    return ApplicationError(refusal.code, type=refusal.code, non_retryable=True)


class CaseActivities:
    def __init__(self, desk: Desk, staff: StaffQueue, evidence=None):
        self.desk = desk
        self.staff = staff
        self.evidence = evidence or NoEvidence()

    @activity.defn(name="prepare")
    def prepare(self, request: CaseRequest) -> Prepared:
        scope = CustomerScope(request.tenant, request.customer)
        try:
            settled = self.desk.settled_for(scope, request.booking_id)
            if settled is not None:
                return Prepared(settled=settled)
            proposal = self.desk.propose(
                scope, request.case_id, request.booking_id, request.intent, request.storm_claim
            )
        except Refusal as refusal:
            return Prepared(code=refusal.code)
        return Prepared(proposal=proposal)

    @activity.defn(name="recheck")
    def recheck(self, proposal: Proposal, consent: Consent | None, review: Review | None) -> Gate:
        try:
            return Gate(grant_id=self.desk.recheck(proposal, consent, review))
        except Refusal as refusal:
            return Gate(code=refusal.code)

    @activity.defn(name="recheck_resolution")
    def recheck_resolution(
        self, resolution: Resolution, proposal: Proposal, consent: Consent | None, review: Review | None
    ) -> Gate:
        principal = Principal(resolution.tenant, resolution.actor, resolution.roles)
        try:
            return Gate(grant_id=self.desk.recheck_resolution(principal, proposal, consent, review))
        except Refusal as refusal:
            return Gate(code=refusal.code)

    @activity.defn(name="cancel_booking")
    def cancel_booking(self, proposal: Proposal) -> None:
        try:
            self.desk.cancel(proposal)
        except Refusal as refusal:
            raise _stop_retrying(refusal) from None

    @activity.defn(name="issue_refund")
    def issue_refund(self, proposal: Proposal) -> None:
        try:
            self.desk.refund(proposal)
        except Refusal as refusal:
            raise _stop_retrying(refusal) from None

    @activity.defn(name="read_back")
    def read_back(self, proposal: Proposal) -> Observation:
        try:
            return self.desk.observe(proposal)
        except Refusal as refusal:
            return Observation(state="unknown", code=refusal.code)

    @activity.defn(name="open_staff_item")
    def open_staff_item(self, request: CaseRequest, operation_id: str | None, code: str, owner: str) -> None:
        self.staff.open(request.tenant, request.case_id, operation_id, code, owner, self.desk.clock().isoformat())

    @activity.defn(name="close_staff_item")
    def close_staff_item(self, request: CaseRequest, actor: str, how: str) -> None:
        self.staff.close(request.tenant, request.case_id, actor, how, self.desk.clock().isoformat())

    @activity.defn(name="record_decision")
    def record_decision(self, proposal: Proposal) -> DecisionRef:
        scope = CustomerScope(proposal.tenant, proposal.customer)
        try:
            return self.evidence.decide(proposal, self.desk.backend.owned(scope, proposal.booking_id))
        except Refusal as refusal:
            return DecisionRef(code=refusal.code)

    @activity.defn(name="record_review")
    def record_review(self, decision: DecisionRef, review: Review) -> str | None:
        return self.evidence.review(decision, review)

    @activity.defn(name="record_effect")
    def record_effect(self, decision: DecisionRef, status: str, review_id: str | None, code: str | None) -> None:
        self.evidence.effect(decision, status, review_id, code)

    def registered(self) -> list:
        return [
            self.prepare,
            self.recheck,
            self.recheck_resolution,
            self.cancel_booking,
            self.issue_refund,
            self.read_back,
            self.open_staff_item,
            self.close_staff_item,
            self.record_decision,
            self.record_review,
            self.record_effect,
        ]
