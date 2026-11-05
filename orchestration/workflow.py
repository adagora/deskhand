import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TemporalError

with workflow.unsafe.imports_passed_through():
    from casework.model import (
        CaseRequest,
        CaseResult,
        Consent,
        DecisionRef,
        Gate,
        Observation,
        Prepared,
        Proposal,
        Resolution,
        Review,
        Timing,
        consent_binds,
        is_authorized,
        is_rejected,
        review_binds,
    )

OWNER = "refund-ops"
STAFF_CODES = ("staff_required_for_claim", "staff_required_for_payment_route")


def _refusal_code(error: BaseException) -> str:
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, ApplicationError) and seen.type:
            return seen.type
        seen = seen.cause if isinstance(seen, TemporalError) else seen.__cause__
    return ""


def _retry(timing: Timing) -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=timing.first_retry_s),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=timing.max_retry_s),
    )


@workflow.defn(name="cancel-refund")
class CancelRefund:
    def __init__(self) -> None:
        self._phase = "starting"
        self._timing = Timing()
        self._proposal: Proposal | None = None
        self._consent: Consent | None = None
        self._review: Review | None = None
        self._resolution: Resolution | None = None
        self._staff_code: str | None = None
        self._resolution_refusal: str | None = None
        self._decision = DecisionRef()
        self._review_id: str | None = None
        self._evidence = "not-recorded"

    @workflow.run
    async def run(self, request: CaseRequest) -> CaseResult:
        self._timing = request.timing
        try:
            prepared: Prepared = await self._read("prepare", [request], Prepared)
        except ActivityError:
            return await self._own(request, "backend_evidence_unavailable")
        if prepared.settled:
            return self._settled(prepared.settled, "already_settled", operation_id=None)
        if prepared.code in STAFF_CODES:
            return await self._own(request, prepared.code)
        if prepared.code:
            return self._closed("refused", prepared.code)
        self._proposal = prepared.proposal
        if not await self._record_decision():
            return await self._own(request, self._staff_code or "evidence_unavailable")
        verdict = await self._wait_for_authority()
        if verdict == "expired":
            await self._record_effect("skipped", "quote_expired")
            return self._closed("quote_expired", "quote_expired")
        await self._record_review()
        if verdict == "rejected":
            await self._record_effect("skipped", "operation_rejected")
            return self._closed("rejected", "operation_rejected")
        return await self._execute(request)

    @workflow.signal(name="consent")
    def submit_consent(self, consent: Consent) -> None:
        if consent_binds(consent, self._proposal):
            self._consent = consent
            if self._proposal.review_required and self._review is None:
                self._phase = "waiting-for-review"

    @workflow.signal(name="review")
    def submit_review(self, review: Review) -> None:
        if self._review is None and review_binds(review, self._proposal):
            self._review = review

    @workflow.signal(name="resolve")
    def submit_resolution(self, resolution: Resolution) -> None:
        if self._staff_code is not None:
            self._resolution = resolution

    @workflow.query(name="phase")
    def current_phase(self) -> str:
        return self._phase

    @workflow.query(name="proposal")
    def current_proposal(self) -> Proposal | None:
        return self._proposal

    @workflow.query(name="staff_code")
    def current_staff_code(self) -> str | None:
        return self._staff_code

    @workflow.query(name="resolution_refusal")
    def latest_resolution_refusal(self) -> str | None:
        return self._resolution_refusal

    async def _wait_for_authority(self) -> str:
        proposal = self._proposal
        self._phase = "waiting-for-consent"
        remaining = (datetime.fromisoformat(proposal.expires_at) - workflow.now()).total_seconds()
        try:
            await workflow.wait_condition(
                lambda: is_authorized(proposal, self._consent, self._review) or is_rejected(proposal, self._review),
                timeout=timedelta(seconds=max(remaining, 0.0)),
            )
        except asyncio.TimeoutError:
            return "expired"
        return "rejected" if is_rejected(proposal, self._review) else "authorized"

    async def _execute(self, request: CaseRequest) -> CaseResult:
        proposal = self._proposal
        self._phase = "rechecking"
        try:
            gate: Gate = await self._read("recheck", [proposal, self._consent, self._review], Gate)
        except ActivityError:
            return await self._own(request, "backend_evidence_unavailable")
        if gate.code:
            await self._record_effect("skipped", gate.code)
            return self._closed("quote_expired" if gate.code == "proposal_expired" else "refused", gate.code)
        write_refusal = await self._dispatch(proposal)
        return await self._conclude(request, await self._read_back(), write_refusal)

    async def _dispatch(self, proposal: Proposal) -> str | None:
        try:
            self._phase = "cancelling"
            await self._write("cancel_booking", proposal)
            self._phase = "refunding"
            await self._write("issue_refund", proposal)
        except ActivityError as error:
            return _refusal_code(error) or "write_unconfirmed"
        return None

    async def _read_back(self) -> Observation:
        self._phase = "reading-back"
        try:
            return await self._read("read_back", [self._proposal], Observation)
        except ActivityError:
            return Observation(state="unknown", code="backend_evidence_unavailable")

    async def _conclude(self, request: CaseRequest, observed: Observation, write_refusal: str | None) -> CaseResult:
        if observed.state == "settled":
            await self._record_effect("done", None)
            return self._settled(observed, "completed", operation_id=self._proposal.id)
        if observed.state == "mismatch":
            return await self._own(request, "readback_mismatch")
        if observed.state == "cancelled-only":
            return await self._own(request, "cancelled_refund_unsettled")
        if observed.state == "changed":
            await self._record_effect("skipped", "booking_changed")
            return self._closed("refused", "booking_changed")
        if observed.state == "none":
            return await self._own(request, "dispatch_not_applied")
        return await self._own(request, observed.code or write_refusal or "backend_evidence_unavailable")

    async def _own(self, request: CaseRequest, code: str) -> CaseResult:
        self._phase = "owned-exception"
        self._staff_code = code
        operation_id = self._proposal.id if self._proposal else None
        await self._record_effect("failed", code)
        await workflow.execute_activity(
            "open_staff_item",
            args=[request, operation_id, code, OWNER],
            start_to_close_timeout=timedelta(seconds=self._timing.attempt_timeout_s),
            retry_policy=_retry(self._timing),
        )
        while True:
            await workflow.wait_condition(lambda: self._resolution is not None)
            resolution, self._resolution = self._resolution, None
            if resolution.kind == "close":
                await self._close_item(request, resolution, "closed-by-staff")
                return self._closed("closed_by_staff", self._staff_code, resolved_by=resolution.actor)
            finished = await self._complete_refund(request, resolution)
            if finished is not None:
                return finished

    async def _complete_refund(self, request: CaseRequest, resolution: Resolution) -> CaseResult | None:
        if resolution.kind != "complete-refund" or self._proposal is None:
            self._resolution_refusal = "invalid_resolution"
            return None
        try:
            gate: Gate = await self._read(
                "recheck_resolution", [resolution, self._proposal, self._consent, self._review], Gate
            )
        except ActivityError:
            self._resolution_refusal = "backend_evidence_unavailable"
            return None
        if gate.code:
            self._resolution_refusal = gate.code
            return None
        self._phase = "refunding"
        try:
            await self._write("issue_refund", self._proposal)
        except ActivityError as error:
            self._resolution_refusal = _refusal_code(error) or "write_unconfirmed"
        observed = await self._read_back()
        self._phase = "owned-exception"
        if observed.state != "settled":
            self._resolution_refusal = observed.code or self._resolution_refusal or "refund_unsettled"
            return None
        await self._close_item(request, resolution, "complete-refund")
        await self._record_effect("done", None)
        return self._settled(observed, "completed", self._proposal.id, resolved_by=resolution.actor)

    async def _close_item(self, request: CaseRequest, resolution: Resolution, how: str) -> None:
        await workflow.execute_activity(
            "close_staff_item",
            args=[request, resolution.actor, how],
            start_to_close_timeout=timedelta(seconds=self._timing.attempt_timeout_s),
            retry_policy=_retry(self._timing),
        )

    async def _record_decision(self) -> bool:
        try:
            self._decision = await self._read("record_decision", [self._proposal], DecisionRef)
        except ActivityError:
            self._staff_code = "evidence_unavailable"
            return False
        if self._decision.code:
            self._staff_code = self._decision.code
            return False
        if self._decision.id:
            self._evidence = "recorded"
        return True

    async def _record_review(self) -> None:
        if not self._decision.id or self._review is None:
            return
        try:
            self._review_id = await self._read("record_review", [self._decision, self._review], str)
        except ActivityError:
            self._evidence = "gap"

    async def _record_effect(self, status: str, code: str | None) -> None:
        if not self._decision.id:
            return
        try:
            await workflow.execute_activity(
                "record_effect",
                args=[self._decision, status, self._review_id, code],
                schedule_to_close_timeout=timedelta(seconds=self._timing.read_deadline_s),
                start_to_close_timeout=timedelta(seconds=self._timing.attempt_timeout_s),
                retry_policy=_retry(self._timing),
            )
        except ActivityError:
            self._evidence = "gap"

    async def _read(self, name: str, args: list, result_type):
        return await workflow.execute_activity(
            name,
            args=args,
            result_type=result_type,
            schedule_to_close_timeout=timedelta(seconds=self._timing.read_deadline_s),
            start_to_close_timeout=timedelta(seconds=self._timing.attempt_timeout_s),
            retry_policy=_retry(self._timing),
        )

    async def _write(self, name: str, proposal: Proposal) -> None:
        await workflow.execute_activity(
            name,
            proposal,
            schedule_to_close_timeout=timedelta(seconds=self._timing.write_deadline_s),
            start_to_close_timeout=timedelta(seconds=self._timing.attempt_timeout_s),
            retry_policy=_retry(self._timing),
        )

    def _settled(
        self, observed: Observation, status: str, operation_id: str | None, resolved_by: str | None = None
    ) -> CaseResult:
        self._phase = status
        return CaseResult(
            status=status,
            operation_id=operation_id,
            amount_minor=observed.amount_minor,
            currency=observed.currency,
            payment_kind=observed.payment_kind,
            claimed_after_readback=True,
            resolved_by=resolved_by,
            evidence=self._evidence,
        )

    def _closed(self, status: str, code: str | None, resolved_by: str | None = None) -> CaseResult:
        self._phase = status
        owned = status == "closed_by_staff"
        return CaseResult(
            status=status,
            code=code,
            owner=OWNER if owned else None,
            operation_id=self._proposal.id if self._proposal else None,
            resolved_by=resolved_by,
            evidence=self._evidence,
        )
