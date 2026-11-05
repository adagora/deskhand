import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment

from casework.model import Refusal
from cockpit.evidence import AuditLogReader
from cockpit.quality import EvalRuns
from cockpit.service import CockpitService
from orchestration.activities import CaseActivities
from orchestration.gateway import Gateway
from orchestration.home import build_worker, seed_home

CORRELATION = "conv-test0001"


def write_log(path: Path) -> None:
    rows = [
        {"kind": "llm-call", "id": "c1", "ts": "2026-11-05T10:00:00.000Z", "correlation": CORRELATION, "model": "gpt-4o-mini", "upstreamStatus": 200, "decision": {"action": "forward-original"}},
        {"kind": "llm-call", "id": "c2", "ts": "2026-11-05T10:00:01.000Z", "correlation": "conv-other", "model": "gpt-4o-mini", "upstreamStatus": 200},
        {"kind": "decision", "id": "d1", "ts": "2026-11-05T10:00:02.000Z", "useCase": "cancel-refund", "correlation": CORRELATION},
        {"kind": "decision", "id": "d1:qdl", "ts": "2026-11-05T10:00:02.000Z", "refId": "d1", "correlation": CORRELATION},
        {"kind": "review", "id": "r1", "ts": "2026-11-05T10:00:03.000Z", "refId": "d1", "review": {"reviewer": "demo-reviewer", "decision": "approved"}},
        {"kind": "effect", "id": "e1", "ts": "2026-11-05T10:00:04.000Z", "refId": "d1", "effect": {"action": "quote-refund-to-card", "status": "done"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class CockpitServiceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.env = cls.loop.run_until_complete(WorkflowEnvironment.start_local(dev_server_log_level="error", ui=False))

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.env.shutdown())
        cls.loop.close()

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home, self.tokens = seed_home(self.root / "home")
        self.client = await Client.connect(self.env.client.service_client.config.target_host)
        self.queue = f"casework-{uuid4().hex}"
        self.prefix = f"case-{uuid4().hex}"
        self.gateway = Gateway(self.client, self.home.directory, self.queue, self.prefix)
        self.log = self.root / "audit.jsonl"
        write_log(self.log)
        self.service = CockpitService(
            self.client, self.home, prefix=self.prefix, task_queue=self.queue, audit=AuditLogReader(self.log),
        )
        self.reviewer, self.customer = self.tokens["supervisor"], self.tokens["customer"]

    def _worker(self):
        return build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff), self.queue)

    async def _stop(self, case_id: str) -> None:
        try:
            await self.gateway.handle_for(self.customer, case_id).terminate("test is done")
        except RPCError:
            pass

    async def _open(self, booking: str = "CW-1002") -> tuple[str, str]:
        case_id = await self.gateway.open_case(self.customer, booking, correlation=CORRELATION)
        for _ in range(100):
            proposal = await self.gateway.quote(self.customer, case_id)
            if proposal is not None:
                self.addAsyncCleanup(self._stop, case_id)
                return case_id, proposal.digest()
            await asyncio.sleep(0.1)
        self.fail("no quote")

    async def test_a_customer_token_cannot_use_the_cockpit(self):
        with self.assertRaises(Refusal) as seen:
            await self.service.cases(self.customer)
        self.assertEqual(seen.exception.code, "staff_only")
        with self.assertRaises(Refusal) as unknown:
            await self.service.cases("not-a-token")
        self.assertEqual(unknown.exception.code, "unauthenticated")

    async def test_a_case_moves_from_waiting_for_the_customer_to_needing_a_review_and_then_done(self):
        async with self._worker():
            case_id, digest = await self._open()
            row = (await self.service.cases(self.reviewer))[0]
            self.assertEqual((row["needs"], row["phase"]), ("customer", "waiting-for-consent"))
            self.assertEqual(row["proposal"]["amount_minor"], 34000)
            detail = await self.service.case(self.reviewer, case_id)
            self.assertTrue(detail["can_review"], "a reviewer may review before the customer confirms")
            self.assertEqual(await self.service.todo(self.reviewer), [])

            await self.gateway.consent(self.customer, case_id, digest)
            for _ in range(50):
                if (await self.service.cases(self.reviewer))[0]["needs"] == "review":
                    break
                await asyncio.sleep(0.1)
            todo = await self.service.todo(self.reviewer)
            self.assertEqual([item["kind"] for item in todo], ["review"])
            self.assertIn("340.00 EUR", todo[0]["title"])
            self.assertEqual(todo[0]["case_id"], case_id)

            with self.assertRaises(Refusal) as stale:
                await self.service.review(self.reviewer, case_id, "approved", "0" * 64)
            self.assertEqual(stale.exception.code, "quote_changed")
            finished = await self.service.review(self.reviewer, case_id, "approved", digest)
            for _ in range(100):
                finished = await self.service.case(self.reviewer, case_id)
                if finished["status"] == "COMPLETED":
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(finished["status"], "COMPLETED")
            self.assertEqual(finished["result"]["amount_minor"], 34000)
            self.assertIsNone(finished["reason"])
            self.assertEqual((await self.service.cases(self.reviewer))[0]["booking_id"], "CW-1002")
            labels = [event["label"] for event in finished["timeline"]]
            for expected in ("Case opened", "Prepared the quote", "The customer confirmed the quote", "A reviewer approved the quote", "Issued the refund", "Read the result back"):
                self.assertIn(expected, labels)
            self.assertEqual(labels[0], "Case opened")
            self.assertTrue(finished["timeline"][1]["at"] >= finished["timeline"][0]["at"])

    async def test_the_case_page_joins_the_audit_log_by_correlation_id(self):
        async with self._worker():
            case_id, _ = await self._open()
            detail = await self.service.case(self.reviewer, case_id)
        evidence = detail["evidence"]
        self.assertEqual(detail["correlation"], CORRELATION)
        self.assertEqual([call["id"] for call in evidence["calls"]], ["c1"])
        self.assertEqual([decision["id"] for decision in evidence["decisions"]], ["d1"])
        self.assertEqual(evidence["decisions"][0]["reviews"][0]["verdict"], "approved")
        self.assertEqual(evidence["decisions"][0]["effects"][0]["status"], "done")
        self.assertIn("ai-audit receipt", evidence["decisions"][0]["receipt"])

    async def test_a_case_of_another_tenant_is_not_reachable(self):
        with self.assertRaises(Refusal) as seen:
            await self.service.case(self.reviewer, "does-not-exist")
        self.assertEqual(seen.exception.code, "case_not_accessible")

    async def test_authority_lists_the_grant_and_a_reviewer_can_revoke_it(self):
        listing = await self.service.authority(self.reviewer)
        self.assertEqual([(g["tier"], g["state"]) for g in listing["grants"]], [("transact", "active")])
        self.assertTrue(listing["can_revoke"])
        with self.assertRaises(Refusal):
            await self.service.revoke(self.reviewer, listing["grants"][0]["id"], "  ")
        after = await self.service.revoke(self.reviewer, listing["grants"][0]["id"], "Policy under review")
        self.assertEqual(after["grants"][0]["state"], "revoked")
        self.assertEqual(after["grants"][0]["revoke_reason"], "Policy under review")

    async def test_a_grant_that_expires_within_a_week_is_on_the_to_do_list(self):
        later = datetime.now(timezone.utc) + timedelta(days=84)
        service = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue, now=lambda: later)
        items = await service.todo(self.reviewer)
        self.assertIn("authority", [item["kind"] for item in items])

    async def test_health_notices_a_missing_worker_and_a_running_one(self):
        without = {check["id"]: check for check in (await self.service.health(self.reviewer))["checks"]}
        self.assertEqual(without["temporal"]["state"], "ok")
        self.assertEqual(without["worker"]["state"], "bad")
        async with self._worker():
            await asyncio.sleep(0.5)
            with_worker = {check["id"]: check for check in (await self.service.health(self.reviewer))["checks"]}
        self.assertEqual(with_worker["worker"]["state"], "ok")
        self.assertEqual(with_worker["audit-anchor"]["state"], "warn")
        self.assertEqual(with_worker["audit-chain"]["state"], "unknown")

    async def test_quality_is_empty_until_a_folder_is_given(self):
        self.assertEqual(await self.service.quality(self.reviewer), {"configured": False, "runs": []})
        service = CockpitService(self.client, self.home, evals=EvalRuns(self.root / "none"))
        self.assertEqual(await service.quality(self.reviewer), {"configured": True, "runs": []})


if __name__ == "__main__":
    unittest.main()
