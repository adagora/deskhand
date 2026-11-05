import asyncio
import dataclasses
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment

from casework.backend import SqliteBackend
from casework.desk import system_now
from casework.identity import Directory, token_digest
from casework.model import CaseRequest, CaseResult, Consent, CustomerScope, Principal, Proposal, Refusal, Review, Timing
from casework.store import transaction
from citywheels.clock import FIXED_NOW
from citywheels.fake_model import FakeModel
from orchestration.__main__ import ask_and_open_cases
from orchestration.activities import CaseActivities
from orchestration.gateway import Gateway
from orchestration.home import build_worker, seed_home
from orchestration.workflow import CancelRefund

ROOT = Path(__file__).resolve().parents[2]
FAST = Timing(read_deadline_s=5, write_deadline_s=1.5, attempt_timeout_s=1, first_retry_s=0.05, max_retry_s=0.2)
PATIENT = Timing(read_deadline_s=60, write_deadline_s=60, attempt_timeout_s=2, first_retry_s=0.05, max_retry_s=0.2)


class EveryAckLost(SqliteBackend):
    def refund(self, proposal: Proposal, settled_at: str) -> None:
        super().refund(proposal, settled_at)
        raise ConnectionError("acknowledgement lost")


class RefundDown(SqliteBackend):
    def refund(self, proposal: Proposal, settled_at: str) -> None:
        raise ConnectionError("payment system down")


class ReadBackFailsOnce(SqliteBackend):
    failed = False

    def observe(self, proposal: Proposal):
        if not self.failed:
            self.failed = True
            raise ConnectionError("read timed out")
        return super().observe(proposal)


class ReadBackAlwaysFails(SqliteBackend):
    def observe(self, proposal: Proposal):
        raise ConnectionError("read timed out")


class Case:
    def __init__(self, test: "WorkflowTests", case_id: str):
        self.test = test
        self.id = case_id

    @property
    def handle(self):
        return self.test.gateway.handle_for(self.test.tokens["customer"], self.id)

    async def proposal(self) -> Proposal:
        await self.wait_for_phase("waiting-for-consent", "waiting-for-review")
        return await self.handle.query(CancelRefund.current_proposal)

    async def wait_for_phase(self, *phases: str, timeout: float = 20) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last = None
        while loop.time() < deadline:
            last = await self.handle.query(CancelRefund.current_phase)
            if last in phases:
                return last
            description = await self.handle.describe()
            if description.status != WorkflowExecutionStatus.RUNNING:
                raise AssertionError(f"case closed in {last}, wanted {phases}: {description.status}")
            await asyncio.sleep(0.02)
        raise AssertionError(f"last phase {last}, wanted {phases}")

    async def consent_and_review(self) -> Proposal:
        proposal = await self.proposal()
        await self.test.gateway.consent(self.test.tokens["customer"], self.id, proposal.digest())
        await self.test.gateway.review(self.test.tokens["reviewer"], self.id, "approved", proposal.digest())
        return proposal

    async def result(self) -> CaseResult:
        return await asyncio.wait_for(self.handle.result(), timeout=30)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.env = cls.loop.run_until_complete(
            WorkflowEnvironment.start_local(dev_server_log_level="error", ui=False)
        )

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.env.shutdown())
        cls.loop.close()

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home, self.tokens = seed_home(self.root)
        self.client = await Client.connect(self._address())
        self.queue = f"casework-{uuid4().hex}"
        self.prefix = f"case-{uuid4().hex}"
        self.gateway = Gateway(self.client, self.home.directory, self.queue, self.prefix)

    def _address(self) -> str:
        return self.env.client.service_client.config.target_host

    def _worker(self, backend=None, **desk):
        built = self.home.desk(**desk)
        if backend is not None:
            built.backend = backend
        return build_worker(self.client, CaseActivities(built, self.home.staff), self.queue)

    async def _open(self, booking_id: str = "CW-1002", timing: Timing = PATIENT, **options) -> Case:
        return Case(self, await self.gateway.open_case(self.tokens["customer"], booking_id, timing=timing, **options))

    def _backend(self, backend_type):
        return backend_type(self.root / "backend.db")

    async def test_a_case_ends_in_a_refund_only_after_consent_review_and_read_back(self):
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            self.assertEqual(proposal.amount_minor, 34000)
            self.assertEqual(self.home.backend.refund_count(), 0)
            await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
            await case.wait_for_phase("waiting-for-review")
            self.assertEqual(self.home.backend.refund_count(), 0)
            await self.gateway.review(self.tokens["reviewer"], case.id, "approved", proposal.digest())
            result = await case.result()
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.claimed_after_readback)
        self.assertEqual((result.amount_minor, result.currency, result.payment_kind), (34000, "EUR", "card"))
        self.assertEqual(result.evidence, "not-recorded")
        self.assertEqual(self.home.backend.refund_count(), 1)

    async def test_a_clear_request_opens_a_case_and_returns_its_exact_quote(self):
        async with self._worker():
            for booking_id, amount in (("CW-1001", 9000), ("CW-1002", 34000), ("CW-1004", 7300)):
                asked = await ask_and_open_cases(
                    self.gateway, self.tokens["customer"], f"Please cancel booking {booking_id}.", FakeModel(),
                    self.home.backend,
                )
                self.assertEqual(len(asked["cases"]), 1, asked)
                case = asked["cases"][0]
                self.assertEqual(case["phase"], "waiting-for-consent")
                self.assertEqual(case["quote"]["amount_minor"], amount)
                self.assertEqual(self.home.backend.refund_count(), 0)
                await Case(self, case["case_id"]).handle.terminate("test is done")

    async def test_a_price_question_opens_no_case(self):
        asked = await ask_and_open_cases(
            self.gateway, self.tokens["customer"], "How much would I get back if I cancel CW-1002?", FakeModel(),
            self.home.backend,
        )
        self.assertEqual(asked["cases"], [])

    async def test_a_customer_cannot_open_a_case_for_a_booking_that_is_not_theirs(self):
        with transaction(self.home.backend.path) as connection:
            connection.execute("UPDATE booking SET customer='someone-else' WHERE id='CW-1002'")
        async with self._worker():
            asked = await ask_and_open_cases(
                self.gateway, self.tokens["customer"], "Please cancel booking CW-1002.", FakeModel(),
                self.home.backend,
            )
        self.assertEqual(asked["cases"], [])
        self.assertIn("was not found", asked["answer"])

    async def test_a_small_refund_runs_on_consent_alone(self):
        with transaction(self.home.backend.path) as connection:
            connection.execute("UPDATE booking SET deposit_minor=1000 WHERE id='CW-1002'")
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            self.assertFalse(proposal.review_required)
            await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
            result = await case.result()
        self.assertEqual((result.status, result.amount_minor), ("completed", 5000))

    async def test_an_unbound_approval_leaves_the_case_waiting(self):
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            await self.gateway.consent(self.tokens["customer"], case.id, "0" * 64)
            await self.gateway.review(self.tokens["reviewer"], case.id, "approved", "0" * 64)
            await case.handle.signal(CancelRefund.submit_consent, Consent("someone-else", proposal.digest(), "now"))
            await case.handle.signal(CancelRefund.submit_review, Review(proposal.customer, "approved", proposal.digest(), "now"))
            await asyncio.sleep(0.5)
            self.assertEqual(await case.handle.query(CancelRefund.current_phase), "waiting-for-consent")
            self.assertEqual(self.home.backend.refund_count(), 0)
            await case.consent_and_review()
            self.assertEqual((await case.result()).status, "completed")

    async def test_a_rejection_closes_the_case_without_a_write(self):
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            await self.gateway.review(self.tokens["reviewer"], case.id, "rejected", proposal.digest())
            result = await case.result()
        self.assertEqual((result.status, result.code), ("rejected", "operation_rejected"))
        self.assertEqual(self.home.backend.refund_count(), 0)
        self.assertEqual(self.home.backend.owned(_scope(), "CW-1002").status, "confirmed")

    async def test_the_first_bound_review_wins(self):
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            await self.gateway.review(self.tokens["reviewer"], case.id, "approved", proposal.digest())
            await self.gateway.review(self.tokens["supervisor"], case.id, "rejected", proposal.digest())
            await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
            result = await case.result()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.home.backend.refund_count(), 1)

    async def test_no_consent_expires_the_quote_on_a_timer_and_writes_nothing(self):
        async with self._worker(quote_ttl=timedelta(seconds=2)):
            case = await self._open()
            old = await case.proposal()
            result = await case.result()
            self.assertEqual((result.status, result.code), ("quote_expired", "quote_expired"))
            self.assertEqual(self.home.backend.refund_count(), 0)
            again = await self._open()
            new = await again.proposal()
        self.assertEqual(again.id, case.id)
        self.assertNotEqual(new.digest(), old.digest())

    async def test_a_new_run_after_expiry_does_not_inherit_the_old_consent(self):
        async with self._worker(quote_ttl=timedelta(seconds=30)):
            first = await self._open()
            old = await first.proposal()
            await first.handle.terminate("test ends the first quote")
            second = await self._open()
            new = await second.proposal()
            await self.gateway.consent(self.tokens["customer"], second.id, old.digest())
            await self.gateway.review(self.tokens["reviewer"], second.id, "approved", old.digest())
            await asyncio.sleep(0.5)
            self.assertEqual(await second.handle.query(CancelRefund.current_phase), "waiting-for-consent")
            await self.gateway.consent(self.tokens["customer"], second.id, new.digest())
            await self.gateway.review(self.tokens["reviewer"], second.id, "approved", new.digest())
            self.assertEqual((await second.result()).status, "completed")
        self.assertEqual(self.home.backend.refund_count(), 1)

    async def test_the_wait_has_no_short_deadline(self):
        async with self._worker():
            case = await self._open()
            await case.proposal()
            config = (await case.handle.describe()).raw_description.execution_config
            self.assertEqual(config.workflow_execution_timeout.ToTimedelta(), timedelta(0))
            self.assertEqual(config.workflow_run_timeout.ToTimedelta(), timedelta(0))
            await case.handle.terminate("test is done")

    async def test_every_acknowledgement_lost_still_reads_back_the_refund(self):
        async with self._worker(self._backend(EveryAckLost)):
            case = await self._open(timing=FAST)
            await case.consent_and_review()
            result = await case.result()
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.claimed_after_readback)
        self.assertEqual(self.home.backend.refund_count(), 1)
        self.assertEqual(self.home.staff.items("citywheels", "open"), [])

    async def test_one_failed_read_back_is_retried(self):
        async with self._worker(self._backend(ReadBackFailsOnce)):
            case = await self._open()
            await case.consent_and_review()
            result = await case.result()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.home.backend.refund_count(), 1)

    async def test_a_read_back_that_never_answers_stays_an_owned_exception(self):
        async with self._worker(self._backend(ReadBackAlwaysFails)):
            case = await self._open(timing=FAST)
            await case.consent_and_review()
            await case.wait_for_phase("owned-exception")
            self.assertEqual(await case.handle.query(CancelRefund.current_staff_code), "backend_evidence_unavailable")
            self.assertEqual(self.home.backend.refund_count(), 1)
            items = self.home.staff.items("citywheels", "open")
            self.assertEqual([(item.code, item.owner) for item in items], [("backend_evidence_unavailable", "refund-ops")])
            await self.gateway.resolve(self.tokens["supervisor"], case.id, "close")
            result = await case.result()
        self.assertEqual((result.status, result.resolved_by), ("closed_by_staff", "demo-supervisor"))
        self.assertFalse(result.claimed_after_readback)
        self.assertEqual(self.home.staff.items("citywheels", "open"), [])

    async def test_a_refund_failure_after_cancel_is_owned_and_a_supervisor_completes_it(self):
        async with self._worker(self._backend(RefundDown)):
            case = await self._open(timing=FAST)
            await case.consent_and_review()
            await case.wait_for_phase("owned-exception")
            self.assertEqual(await case.handle.query(CancelRefund.current_staff_code), "cancelled_refund_unsettled")
            self.assertEqual(self.home.backend.owned(_scope(), "CW-1002").status, "cancelled")
            self.assertEqual(self.home.backend.refund_count(), 0)
            with self.assertRaises(Refusal):
                await self.gateway.resolve(self.tokens["reviewer"], case.id, "complete-refund")
            self.assertEqual(self.home.backend.refund_count(), 0)
            handle = case.handle
        async with self._worker():
            await self.gateway.resolve(self.tokens["supervisor"], case.id, "complete-refund")
            result = await asyncio.wait_for(handle.result(), timeout=30)
        self.assertEqual((result.status, result.resolved_by), ("completed", "demo-supervisor"))
        self.assertTrue(result.claimed_after_readback)
        self.assertEqual(self.home.backend.refund_count(), 1)
        closed = self.home.staff.items("citywheels", "closed")
        self.assertEqual([(item.code, item.closed_how) for item in closed], [("cancelled_refund_unsettled", "complete-refund")])

    async def test_a_ledger_mismatch_is_owned_and_cannot_be_completed(self):
        async with self._worker():
            case = await self._open()
            await case.consent_and_review()
            await case.result()
        with transaction(self.home.backend.path) as connection:
            connection.execute("UPDATE refund SET amount_minor=amount_minor+1")
            connection.execute("UPDATE booking SET status='confirmed', cancelled_by=NULL, version=1")
        async with self._worker():
            again = await self._open(timing=FAST)
            await again.consent_and_review()
            await again.wait_for_phase("owned-exception")
            self.assertEqual(await again.handle.query(CancelRefund.current_staff_code), "readback_mismatch")
            await self.gateway.resolve(self.tokens["supervisor"], again.id, "complete-refund")
            await asyncio.sleep(0.5)
            self.assertEqual(await again.handle.query(CancelRefund.latest_resolution_refusal), "readback_mismatch")
            self.assertEqual(self.home.backend.refund_count(), 1)
            await self.gateway.resolve(self.tokens["supervisor"], again.id, "close")
            self.assertEqual((await again.result()).status, "closed_by_staff")

    async def test_a_duplicate_request_attaches_and_a_later_one_writes_nothing(self):
        async with self._worker():
            first = await self._open()
            second = await self._open()
            self.assertEqual(first.id, second.id)
            await first.consent_and_review()
            result = await first.result()
            self.assertEqual(result.status, "completed")
            ledger = self.home.backend.refunds()
            later = await self._open()
            repeated = await later.result()
        self.assertEqual(repeated.status, "already_settled")
        self.assertTrue(repeated.claimed_after_readback)
        self.assertEqual(repeated.amount_minor, 34000)
        self.assertEqual(self.home.backend.refunds(), ledger)

    async def test_a_grant_revoked_during_the_wait_refuses_the_effect(self):
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            supervisor = Principal("citywheels", "demo-supervisor", ["reviewer", "operator", "deployer"])
            grant = self.home.grants.authorize(
                tenant="citywheels", intent=proposal.intent, action=proposal.action, amount_minor=proposal.amount_minor,
                currency="EUR", release=proposal.release, now=_now(),
            )
            self.home.grants.revoke(supervisor, grant.id, "regression", _now())
            await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
            await self.gateway.review(self.tokens["reviewer"], case.id, "approved", proposal.digest())
            result = await case.result()
        self.assertEqual((result.status, result.code), ("refused", "revoked"))
        self.assertEqual(self.home.backend.refund_count(), 0)
        self.assertEqual(self.home.backend.owned(_scope(), "CW-1002").status, "confirmed")

    async def test_a_refund_band_change_during_the_wait_refuses_the_old_amount(self):
        shop = {"now": _shop(hours=0)}
        async with self._worker(business_clock=lambda: shop["now"]):
            case = await self._open()
            proposal = await case.proposal()
            shop["now"] = _shop(hours=40)
            await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
            await self.gateway.review(self.tokens["reviewer"], case.id, "approved", proposal.digest())
            result = await case.result()
        self.assertEqual((result.status, result.code), ("refused", "amount_changed"))
        self.assertEqual(self.home.backend.refund_count(), 0)

    async def test_the_caller_cannot_choose_the_refund_clock(self):
        self.assertNotIn("business_at", {field.name for field in dataclasses.fields(CaseRequest)})

    async def test_a_storm_claim_goes_to_an_owned_staff_item(self):
        async with self._worker():
            case = await self._open(storm_claim=True)
            await case.wait_for_phase("owned-exception")
            items = self.home.staff.items("citywheels", "open")
            self.assertEqual([item.code for item in items], ["staff_required_for_claim"])
            self.assertEqual(self.home.backend.owned(_scope(), "CW-1002").status, "confirmed")
            await self.gateway.resolve(self.tokens["supervisor"], case.id, "complete-refund")
            await asyncio.sleep(0.3)
            self.assertEqual(await case.handle.query(CancelRefund.latest_resolution_refusal), "invalid_resolution")
            await self.gateway.resolve(self.tokens["supervisor"], case.id, "close")
            self.assertEqual((await case.result()).status, "closed_by_staff")
        self.assertEqual(self.home.backend.refund_count(), 0)

    async def test_a_booking_that_is_not_confirmed_is_refused_without_a_staff_item(self):
        async with self._worker():
            case = await self._open("CW-1008")
            result = await case.result()
        self.assertEqual((result.status, result.code), ("refused", "not_cancellable"))
        self.assertEqual(self.home.staff.items("citywheels"), [])

    async def test_another_customer_cannot_see_or_consent_on_a_case(self):
        entries = json.loads((self.root / "identities.json").read_text(encoding="utf-8"))
        entries.append({"sha256": token_digest("other-customer"), "tenant": "citywheels", "id": "other-customer", "roles": ["customer"]})
        gateway = Gateway(self.client, Directory(entries), self.queue, self.prefix)
        async with self._worker():
            case = await self._open()
            proposal = await case.proposal()
            with self.assertRaises(Refusal) as seen:
                await gateway.quote("other-customer", case.id)
            self.assertEqual(seen.exception.code, "case_not_accessible")
            await gateway.consent("other-customer", case.id, proposal.digest())
            await asyncio.sleep(0.3)
            self.assertEqual(await case.handle.query(CancelRefund.current_phase), "waiting-for-consent")
            other = await gateway.open_case("other-customer", "CW-1002", timing=PATIENT)
            result = await gateway.handle_for("other-customer", other).result()
            self.assertEqual((result.status, result.code), ("refused", "booking_not_accessible"))
            with self.assertRaises(Refusal):
                await self.gateway.consent(self.tokens["reviewer"], case.id, proposal.digest())
            with self.assertRaises(Refusal):
                await self.gateway.review(self.tokens["customer"], case.id, "approved", proposal.digest())
            await case.handle.terminate("test is done")

    def _spawn_worker(self, fault: str = "none") -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "orchestration/tests/worker_process.py"), self._address(), str(self.root), fault],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.addCleanup(_stop, process)
        self.assertEqual(process.stdout.readline().strip(), "ready")
        return process

    async def test_the_wait_survives_killing_the_worker_process(self):
        self.gateway = Gateway(self.client, self.home.directory, prefix=self.prefix)
        first = self._spawn_worker()
        case = await self._open()
        proposal = await case.proposal()
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        await self.gateway.consent(self.tokens["customer"], case.id, proposal.digest())
        await self.gateway.review(self.tokens["reviewer"], case.id, "approved", proposal.digest())
        self.assertEqual(self.home.backend.refund_count(), 0)
        second = self._spawn_worker()
        result = await case.result()
        second.kill()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.home.backend.refund_count(), 1)

    async def test_a_worker_that_dies_after_the_cancel_write_leaves_one_refund(self):
        self.gateway = Gateway(self.client, self.home.directory, prefix=self.prefix)
        crashing = self._spawn_worker("crash-after-cancel")
        case = await self._open()
        await case.consent_and_review()
        crashing.wait(timeout=30)
        self.assertEqual(crashing.returncode, 9)
        self.assertEqual(self.home.backend.owned(_scope(), "CW-1002").status, "cancelled")
        self.assertEqual(self.home.backend.refund_count(), 0)
        replacement = self._spawn_worker()
        result = await case.result()
        replacement.kill()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.home.backend.refund_count(), 1)


def _stop(process: subprocess.Popen) -> None:
    process.kill()
    process.wait()
    process.stdout.close()


def _scope() -> CustomerScope:
    return CustomerScope("citywheels", "demo-customer")


def _now():
    return system_now()


def _shop(hours: int):
    return FIXED_NOW + timedelta(hours=hours)


if __name__ == "__main__":
    unittest.main()
