from datetime import datetime, timezone

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from casework.identity import Directory
from casework.model import (
    CaseRequest,
    Consent,
    Principal,
    Proposal,
    Refusal,
    Resolution,
    Review,
    Timing,
    evidence_digest,
)
from orchestration.workflow import CancelRefund

TASK_QUEUE = "citywheels-casework"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def case_id_for(customer: str, booking_id: str) -> str:
    return evidence_digest({"customer": customer, "booking": booking_id})[:24]


class Gateway:
    def __init__(self, client: Client, directory: Directory, task_queue: str = TASK_QUEUE, prefix: str = "case"):
        self.client = client
        self.directory = directory
        self.task_queue = task_queue
        self.prefix = prefix

    async def open_case(
        self, token: str, booking_id: str, *, storm_claim: bool = False, timing: Timing | None = None
    ) -> str:
        principal = self._customer(token)
        case_id = case_id_for(principal.id, booking_id)
        request = CaseRequest(
            tenant=principal.tenant,
            customer=principal.id,
            case_id=case_id,
            booking_id=booking_id,
            storm_claim=storm_claim,
            timing=timing or Timing(),
        )
        await self.client.start_workflow(
            CancelRefund.run,
            request,
            id=self._workflow_id(principal.tenant, case_id),
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return case_id

    async def quote(self, token: str, case_id: str) -> Proposal | None:
        principal = self.directory.authenticate(token)
        proposal = await self._handle(principal, case_id).query(CancelRefund.current_proposal)
        if proposal is not None and "customer" in principal.roles and proposal.customer != principal.id:
            raise Refusal("case_not_accessible")
        return proposal

    async def phase(self, token: str, case_id: str) -> str:
        principal = self.directory.authenticate(token)
        await self.quote(token, case_id)
        return await self._handle(principal, case_id).query(CancelRefund.current_phase)

    async def consent(self, token: str, case_id: str, proposal_digest: str) -> None:
        principal = self._customer(token)
        await self._handle(principal, case_id).signal(
            CancelRefund.submit_consent, Consent(principal.id, proposal_digest, _now())
        )

    async def review(self, token: str, case_id: str, verdict: str, proposal_digest: str) -> None:
        principal = self.directory.authenticate(token)
        if "reviewer" not in principal.roles:
            raise Refusal("forbidden")
        handle = self._handle(principal, case_id)
        await handle.signal(CancelRefund.submit_review, Review(principal.id, verdict, proposal_digest, _now()))

    async def resolve(self, token: str, case_id: str, kind: str) -> None:
        principal = self.directory.authenticate(token)
        if "operator" not in principal.roles:
            raise Refusal("forbidden")
        handle = self._handle(principal, case_id)
        await handle.signal(
            CancelRefund.submit_resolution,
            Resolution(principal.tenant, principal.id, list(principal.roles), kind, _now()),
        )

    def handle_for(self, token: str, case_id: str) -> WorkflowHandle:
        return self._handle(self.directory.authenticate(token), case_id)

    def _customer(self, token: str) -> Principal:
        principal = self.directory.authenticate(token)
        if "customer" not in principal.roles:
            raise Refusal("forbidden")
        return principal

    def _handle(self, principal: Principal, case_id: str) -> WorkflowHandle:
        return self.client.get_workflow_handle_for(CancelRefund.run, self._workflow_id(principal.tenant, case_id))

    def _workflow_id(self, tenant: str, case_id: str) -> str:
        return f"{self.prefix}-{tenant}-{case_id}"
