import asyncio
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from temporalio.api.enums.v1 import EventType, TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client, WorkflowExecutionStatus

from casework.messages import NEEDS_A_PERSON, explain, phase_label
from casework.model import CaseRequest, CaseResult, Consent, Principal, Proposal, Refusal, Resolution, Review
from cockpit.evidence import AuditLogReader
from cockpit.quality import EvalRuns
from orchestration.gateway import TASK_QUEUE, Gateway
from orchestration.home import Home
from orchestration.workflow import CancelRefund

ROLES = {"operator", "reviewer", "deployer"}
TRANSITIONAL = ("starting", "rechecking", "cancelling", "refunding", "reading-back")
STUCK_AFTER = timedelta(minutes=10)
CLOSED_BOOKINGS = 50
EXPIRING_SOON = timedelta(days=7)
QUERY_TIMEOUT_S = 5
ACTIVITY_LABELS = {
    "prepare": "Prepared the quote",
    "record_decision": "Recorded the decision in the audit log",
    "recheck": "Rechecked the quote against current facts",
    "recheck_resolution": "Rechecked before the supervisor's refund",
    "cancel_booking": "Cancelled the booking",
    "issue_refund": "Issued the refund",
    "read_back": "Read the result back",
    "record_review": "Recorded the review in the audit log",
    "record_effect": "Recorded the outcome in the audit log",
    "open_staff_item": "Opened a staff item",
    "close_staff_item": "Closed the staff item",
}
STATE_ORDER = {"ok": 0, "unknown": 1, "warn": 2, "bad": 3}


def system_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment) -> str | None:
    return moment.isoformat() if moment else None


def _proposal_summary(proposal: Proposal | None) -> dict | None:
    if proposal is None:
        return None
    return {
        "amount_minor": proposal.amount_minor, "currency": proposal.currency, "booking_id": proposal.booking_id,
        "customer": proposal.customer, "review_required": proposal.review_required, "expires_at": proposal.expires_at,
        "digest": proposal.digest(), "correlation": proposal.correlation, "payment_kind": proposal.payment_kind,
    }


class CockpitService:
    def __init__(
        self,
        client: Client,
        home: Home,
        *,
        prefix: str = "case",
        task_queue: str = TASK_QUEUE,
        audit: AuditLogReader | None = None,
        evals: EvalRuns | None = None,
        proxy_url: str | None = None,
        now=system_now,
    ):
        self.client = client
        self.home = home
        self.prefix = prefix
        self.task_queue = task_queue
        self.audit = audit
        self.evals = evals
        self.proxy_url = proxy_url
        self.now = now
        self.gateway = Gateway(client, home.directory, task_queue, prefix)

    def principal(self, token: str, roles=ROLES) -> Principal:
        principal = self.home.directory.authenticate(token)
        if not set(principal.roles) & set(roles):
            raise Refusal("forbidden")
        return principal

    async def me(self, token: str) -> dict:
        principal = self.principal(token)
        return {"id": principal.id, "tenant": principal.tenant, "roles": principal.roles}

    async def cases(self, token: str) -> list[dict]:
        principal = self.principal(token)
        listed = []
        async for workflow in self.client.list_workflows("WorkflowType = 'cancel-refund'"):
            if workflow.id.startswith(self._prefix(principal.tenant)):
                listed.append(workflow)
        gate = asyncio.Semaphore(8)

        async def one(workflow):
            async with gate:
                return await self._row(principal, workflow)

        rows = list(await asyncio.gather(*(one(workflow) for workflow in listed)))
        closed = sorted((row for row in rows if row["status"] != "RUNNING"), key=lambda row: row["closed_at"] or "", reverse=True)[:CLOSED_BOOKINGS]
        for row, booking in zip(closed, await asyncio.gather(*(self._booking_of(row["case_id"], principal) for row in closed))):
            row["booking_id"] = booking
        urgency = {"review": 0, "staff": 1, "stuck": 2, "customer": 3, None: 4}
        return sorted(rows, key=lambda row: (urgency.get(row["needs"], 4), row["started_at"] or ""))

    async def case(self, token: str, case_id: str) -> dict:
        principal = self.principal(token)
        handle = self.client.get_workflow_handle(self._workflow_id(principal.tenant, case_id))
        try:
            description = await handle.describe()
        except Exception:
            raise Refusal("case_not_accessible") from None
        row = await self._row(principal, description, handle=handle)
        history = await self._timeline(handle)
        request = history["request"]
        correlation = (request.correlation if request else "") or (row["proposal"] or {}).get("correlation", "")
        evidence = self.audit.for_correlation(correlation) if self.audit else None
        proposal = row["proposal"]
        running = row["status"] == "RUNNING"
        review_open = bool(
            running and proposal and proposal["review_required"]
            and row["phase"] in ("waiting-for-consent", "waiting-for-review")
            and "reviewer" in principal.roles and principal.id != proposal["customer"]
        )
        kinds = []
        if running and row["phase"] == "owned-exception" and "operator" in principal.roles:
            kinds.append("close")
            if row["staff_code"] == "cancelled_refund_unsettled" and "reviewer" in principal.roles:
                kinds.insert(0, "complete-refund")
        return {
            **row,
            "booking_id": request.booking_id if request else (proposal or {}).get("booking_id"),
            "correlation": correlation,
            "timeline": history["events"],
            "evidence": evidence,
            "can_review": review_open,
            "resolve_kinds": kinds,
        }

    async def review(self, token: str, case_id: str, verdict: str, digest: str) -> dict:
        principal = self.principal(token, {"reviewer"})
        if verdict not in ("approved", "rejected"):
            raise Refusal("invalid_proposal")
        handle = self.client.get_workflow_handle(self._workflow_id(principal.tenant, case_id))
        proposal = await handle.query(CancelRefund.current_proposal)
        if proposal is None or proposal.digest() != digest:
            raise Refusal("quote_changed")
        await self.gateway.review(token, case_id, verdict, digest)
        return await self.case(token, case_id)

    async def resolve(self, token: str, case_id: str, kind: str) -> dict:
        self.principal(token, {"operator"})
        if kind not in ("complete-refund", "close"):
            raise Refusal("invalid_proposal")
        await self.gateway.resolve(token, case_id, kind)
        return await self.case(token, case_id)

    async def todo(self, token: str) -> list[dict]:
        principal = self.principal(token)
        items = []
        for row in await self.cases(token):
            proposal = row["proposal"] or {}
            if row["needs"] == "review":
                items.append({
                    "kind": "review", "case_id": row["case_id"], "since": row["started_at"], "due": proposal.get("expires_at"),
                    "title": f"Review a refund of {_money(proposal)} for {proposal.get('booking_id', 'a booking')}",
                    "detail": "The customer has confirmed. Your review releases the refund." if row["phase"] == "waiting-for-review" else "A reviewer is needed.",
                })
            elif row["needs"] == "staff":
                items.append({
                    "kind": "exception", "case_id": row["case_id"], "since": row["started_at"], "due": None,
                    "title": explain(row["staff_code"] or "")["message"] if row["staff_code"] else "A case needs a person",
                    "detail": explain(row["staff_code"] or "")["next"] if row["staff_code"] else "",
                })
            elif row["needs"] == "stuck":
                items.append({
                    "kind": "stuck", "case_id": row["case_id"], "since": row["started_at"], "due": None,
                    "title": f"A case has not moved from \"{phase_label(row['phase'])}\" for over 10 minutes",
                    "detail": "Check that a worker is running.",
                })
        now = self.now()
        for grant in self.home.grants.listing(principal.tenant, now):
            if grant["state"] == "active" and datetime.fromisoformat(grant["expires_at"]) - now <= EXPIRING_SOON:
                items.append({
                    "kind": "authority", "case_id": None, "since": grant["activated_at"], "due": grant["expires_at"],
                    "title": f"The grant for {grant['intent']} expires soon",
                    "detail": "A reviewer must approve and a deployer must activate a new grant.",
                })
        for check in (await self.health(token))["checks"]:
            if check["state"] == "bad":
                items.append({"kind": "health", "case_id": None, "since": None, "due": None, "title": check["label"], "detail": check["detail"]})
        order = {"review": 0, "exception": 1, "stuck": 2, "authority": 3, "health": 4}
        return sorted(items, key=lambda item: (order[item["kind"]], item["due"] or item["since"] or ""))

    async def authority(self, token: str) -> dict:
        principal = self.principal(token)
        grants = self.home.grants.listing(principal.tenant, self.now())
        return {
            "grants": grants,
            "can_revoke": "reviewer" in principal.roles,
            "tiers": ["shadow", "assist", "contain", "transact"],
        }

    async def revoke(self, token: str, grant_id: str, reason: str) -> dict:
        principal = self.principal(token, {"reviewer"})
        if not reason.strip():
            raise Refusal("invalid_grant")
        self.home.grants.revoke(principal, grant_id, reason.strip()[:300], self.now())
        return await self.authority(token)

    async def quality(self, token: str) -> dict:
        self.principal(token)
        if self.evals is None:
            return {"configured": False, "runs": []}
        return {"configured": True, "runs": self.evals.runs()}

    async def health(self, token: str) -> dict:
        principal = self.principal(token)
        checks = [await self._temporal_check(), await self._worker_check()]
        if self.audit is not None:
            chain = self.audit.chain()
            checks.append({"id": "audit-chain", "label": "Audit log chain", "state": chain["state"], "detail": chain["detail"]})
            checks.append({
                "id": "audit-anchor", "label": "Audit log anchor", "state": "warn",
                "detail": "No external anchor is configured. A rewrite of the whole file would not be caught. Run `ai-audit anchor` daily and keep the anchor file elsewhere.",
            })
        checks.append(await self._proxy_check())
        if self.evals is not None:
            verdict = self.evals.verdict()
            checks.append({"id": "evaluation", "label": "Latest evaluation run", "state": verdict["state"], "detail": verdict["detail"]})
        open_items = len(self.home.staff.items(principal.tenant, "open"))
        checks.append({
            "id": "staff-queue", "label": "Staff items", "state": "warn" if open_items else "ok",
            "detail": f"{open_items} open." if open_items else "None open.",
        })
        worst = max((check["state"] for check in checks), key=lambda state: STATE_ORDER[state])
        return {"overall": worst, "checks": checks}

    async def _temporal_check(self) -> dict:
        try:
            async def probe():
                async for _ in self.client.list_workflows("WorkflowType = 'cancel-refund'", limit=1):
                    break
            await asyncio.wait_for(probe(), QUERY_TIMEOUT_S)
            return {"id": "temporal", "label": "Temporal server", "state": "ok", "detail": "Answering."}
        except Exception:
            return {"id": "temporal", "label": "Temporal server", "state": "bad", "detail": explain("temporal_unreachable")["next"]}

    async def _worker_check(self) -> dict:
        try:
            reply = await asyncio.wait_for(
                self.client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=self.client.namespace,
                        task_queue=TaskQueue(name=self.task_queue),
                        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                    )
                ),
                QUERY_TIMEOUT_S,
            )
        except Exception:
            return {"id": "worker", "label": "Worker", "state": "unknown", "detail": "The worker count could not be read."}
        count = len(reply.pollers)
        if count:
            return {"id": "worker", "label": "Worker", "state": "ok", "detail": f"{count} polling."}
        return {"id": "worker", "label": "Worker", "state": "bad", "detail": "No worker is polling. Start one with `python -m orchestration worker`. Cases wait until one runs."}

    async def _proxy_check(self) -> dict:
        if not self.proxy_url:
            return {"id": "proxy", "label": "ai-audit proxy", "state": "unknown", "detail": "No proxy address is configured."}

        def probe() -> bool:
            try:
                with urllib.request.urlopen(self.proxy_url.rstrip("/") + "/metrics", timeout=3) as reply:
                    return reply.status == 200
            except Exception:
                return False

        up = await asyncio.to_thread(probe)
        return {"id": "proxy", "label": "ai-audit proxy", "state": "ok" if up else "bad", "detail": "Answering." if up else "The proxy did not answer. Model calls fail until it runs."}

    async def _booking_of(self, case_id: str, principal: Principal) -> str | None:
        handle = self.client.get_workflow_handle(self._workflow_id(principal.tenant, case_id))
        try:
            async for event in handle.fetch_history_events(page_size=1):
                if event.WhichOneof("attributes") == "workflow_execution_started_event_attributes":
                    request = await _decode(self.client.data_converter, event.workflow_execution_started_event_attributes.input, CaseRequest)
                    return request.booking_id if request else None
                break
        except Exception:
            return None
        return None

    def _prefix(self, tenant: str) -> str:
        return f"{self.prefix}-{tenant}-"

    def _workflow_id(self, tenant: str, case_id: str) -> str:
        return f"{self._prefix(tenant)}{case_id}"

    async def _row(self, principal: Principal, workflow, handle=None) -> dict:
        handle = handle or self.client.get_workflow_handle(workflow.id, run_id=workflow.run_id)
        running = workflow.status == WorkflowExecutionStatus.RUNNING
        row = {
            "case_id": workflow.id.removeprefix(self._prefix(principal.tenant)),
            "status": workflow.status.name if workflow.status else "UNKNOWN",
            "started_at": _iso(workflow.start_time),
            "closed_at": _iso(workflow.close_time),
            "phase": "unknown", "phase_label": "Not answering", "proposal": None, "staff_code": None,
            "staff": None, "needs": None, "result": None, "booking_id": None,
        }
        if running:
            try:
                row["phase"] = await asyncio.wait_for(handle.query(CancelRefund.current_phase), QUERY_TIMEOUT_S)
                proposal = await asyncio.wait_for(handle.query(CancelRefund.current_proposal), QUERY_TIMEOUT_S)
                row["staff_code"] = await asyncio.wait_for(handle.query(CancelRefund.current_staff_code), QUERY_TIMEOUT_S)
                row["proposal"] = _proposal_summary(proposal)
            except Exception:
                row["phase"] = "unknown"
            row["phase_label"] = phase_label(row["phase"]) if row["phase"] != "unknown" else "Not answering (is a worker running?)"
        else:
            try:
                finished = self.client.get_workflow_handle(workflow.id, run_id=workflow.run_id, result_type=CaseResult)
                result: CaseResult = await asyncio.wait_for(finished.result(), QUERY_TIMEOUT_S)
                row["result"] = asdict(result)
                row["phase"] = result.status
                row["phase_label"] = phase_label(result.status)
            except Exception:
                row["phase"], row["phase_label"] = "ended", "Ended without a result"
        row["needs"] = self._needs(row, workflow)
        if row["staff_code"]:
            row["staff"] = explain(row["staff_code"])
        return row

    def _needs(self, row: dict, workflow) -> str | None:
        if row["status"] != "RUNNING":
            return None
        if row["phase"] == "waiting-for-review":
            return "review"
        if row["phase"] == "owned-exception":
            return "staff"
        if row["phase"] == "waiting-for-consent":
            return "customer"
        if row["phase"] in TRANSITIONAL and workflow.start_time and self.now() - workflow.start_time > STUCK_AFTER:
            return "stuck"
        return None

    async def _timeline(self, handle) -> dict:
        history = await handle.fetch_history()
        converter = self.client.data_converter
        scheduled: dict[int, str] = {}
        events, request = [], None
        for event in history.events:
            which = event.WhichOneof("attributes") or ""
            when = event.event_time.ToDatetime(tzinfo=timezone.utc).isoformat()
            attributes = getattr(event, which, None)
            kind = EventType.Name(event.event_type)
            if kind == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED":
                request = await _decode(converter, attributes.input, CaseRequest)
                events.append({"at": when, "label": "Case opened", "detail": f"Booking {request.booking_id}." if request else "", "state": "ok"})
            elif kind == "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED":
                events.append(await _signal(converter, attributes, when))
            elif kind == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
                scheduled[event.event_id] = attributes.activity_type.name
            elif kind == "EVENT_TYPE_ACTIVITY_TASK_COMPLETED":
                name = scheduled.get(attributes.scheduled_event_id, "")
                events.append({"at": when, "label": ACTIVITY_LABELS.get(name, name), "detail": "", "state": "ok"})
            elif kind == "EVENT_TYPE_ACTIVITY_TASK_FAILED":
                name = scheduled.get(attributes.scheduled_event_id, "")
                message = (attributes.failure.message or "").splitlines()[0:1]
                events.append({"at": when, "label": f"{ACTIVITY_LABELS.get(name, name)}: failed", "detail": message[0] if message else "", "state": "bad"})
            elif kind == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED":
                result = await _decode(converter, attributes.result, CaseResult)
                events.append({"at": when, "label": f"Case finished: {phase_label(result.status) if result else 'done'}", "detail": explain(result.code)["message"] if result and result.code else "", "state": "ok"})
            elif kind in ("EVENT_TYPE_WORKFLOW_EXECUTION_FAILED", "EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED", "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT"):
                events.append({"at": when, "label": "Case ended abnormally", "detail": kind.removeprefix("EVENT_TYPE_WORKFLOW_EXECUTION_").lower(), "state": "bad"})
        return {"events": events, "request": request}


def _money(proposal: dict) -> str:
    if not proposal:
        return "an unknown amount"
    return f"{proposal['amount_minor'] / 100:.2f} {proposal['currency']}"


async def _decode(converter, payloads, hint):
    try:
        decoded = await converter.decode(list(payloads.payloads), [hint])
        return decoded[0] if decoded else None
    except Exception:
        return None


async def _signal(converter, attributes, when: str) -> dict:
    name = attributes.signal_name
    if name == "consent":
        consent: Consent | None = await _decode(converter, attributes.input, Consent)
        return {"at": when, "label": "The customer confirmed the quote", "detail": f"Digest {consent.proposal_digest[:12]}…" if consent else "", "state": "ok"}
    if name == "review":
        review: Review | None = await _decode(converter, attributes.input, Review)
        verdict = review.verdict if review else "reviewed"
        return {"at": when, "label": f"A reviewer {verdict} the quote", "detail": f"By {review.reviewer}." if review else "", "state": "ok" if verdict == "approved" else "warn"}
    if name == "resolve":
        resolution: Resolution | None = await _decode(converter, attributes.input, Resolution)
        return {"at": when, "label": "A person asked to resolve the exception", "detail": f"{resolution.actor}: {resolution.kind}." if resolution else "", "state": "ok"}
    return {"at": when, "label": f"Signal {name}", "detail": "", "state": "ok"}
