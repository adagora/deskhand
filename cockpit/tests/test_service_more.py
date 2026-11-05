import asyncio
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment

from casework.evidence import AuditCli
from casework.model import Refusal
from cockpit.evidence import AuditLogReader
from cockpit.quality import EvalRuns
from cockpit.service import CockpitService
from cockpit.tests.test_service import CORRELATION, write_log
from orchestration.activities import CaseActivities
from orchestration.gateway import Gateway
from orchestration.home import build_worker, seed_home

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT.parents[2] / "ai-audit" / "dist" / "src" / "cli.js"
HAVE_CLI = CLI.exists() and shutil.which("node") is not None


class MoreServiceTests(unittest.IsolatedAsyncioTestCase):
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
        self.queue, self.prefix = f"casework-{uuid4().hex}", f"case-{uuid4().hex}"
        self.gateway = Gateway(self.client, self.home.directory, self.queue, self.prefix)
        self.supervisor, self.reviewer, self.customer = self.tokens["supervisor"], self.tokens["reviewer"], self.tokens["customer"]
        self.log = self.root / "audit.jsonl"
        write_log(self.log)
        self.service = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue, audit=AuditLogReader(self.log))

    async def _stop(self, case_id: str) -> None:
        try:
            await self.gateway.handle_for(self.customer, case_id).terminate("test is done")
        except RPCError:
            pass

    async def _open(self, booking="CW-1002", correlation=CORRELATION) -> str:
        case_id = await self.gateway.open_case(self.customer, booking, correlation=correlation)
        self.addAsyncCleanup(self._stop, case_id)
        for _ in range(100):
            if await self.gateway.quote(self.customer, case_id) is not None:
                return case_id
            await asyncio.sleep(0.1)
        self.fail("no quote")

    async def test_a_reviewer_approves_a_grant_and_a_deployer_activates_it(self):
        before = await self.service.authority(self.supervisor)
        self.assertTrue(before["can_approve"] and before["can_activate"])
        after = await self.service.grant(self.supervisor, "citywheels.cancellation", "assist", 50_000, 30, False)
        approved = next(g for g in after["grants"] if g["tier"] == "assist")
        self.assertEqual((approved["state"], approved["max_amount_minor"]), ("approved", 50_000))
        with self.assertRaises(Refusal) as seen:
            await self.service.activate(self.reviewer, approved["id"])
        self.assertEqual(seen.exception.code, "forbidden", "a reviewer alone cannot activate")
        active = await self.service.activate(self.supervisor, approved["id"])
        states = {g["tier"]: g["state"] for g in active["grants"]}
        self.assertEqual(states, {"assist": "active", "transact": "replaced"})

    async def test_a_grant_needs_a_sane_request_and_a_transact_grant_needs_a_confirmation(self):
        async def attempt(**changes):
            arguments = dict(intent="citywheels.cancellation", tier="assist", max_amount_minor=10_000, days=30, acknowledged=False)
            arguments.update(changes)
            with self.assertRaises(Refusal) as seen:
                await self.service.grant(self.supervisor, **arguments)
            return seen.exception.code

        self.assertEqual(await attempt(tier="transact"), "acknowledge_needed")
        self.assertEqual(await attempt(tier="transact", acknowledged="yes"), "acknowledge_needed")
        self.assertEqual(await attempt(tier="root"), "invalid_grant")
        self.assertEqual(await attempt(intent="other.intent"), "invalid_grant")
        for bad in (0, -5, 500_001, 10.5, True, "100"):
            self.assertEqual(await attempt(max_amount_minor=bad), "grant_limit_invalid", bad)
        for bad in (0, 366, 2.5, True, None):
            self.assertEqual(await attempt(days=bad), "invalid_grant", bad)
        done = await self.service.grant(self.supervisor, "citywheels.cancellation", "transact", 10_000, 30, True)
        self.assertEqual(len(done["grants"]), 2)

    async def test_a_customer_and_an_unknown_token_cannot_change_authority(self):
        for token, code in ((self.customer, "forbidden"), ("nope", "unauthenticated")):
            with self.assertRaises(Refusal) as seen:
                await self.service.grant(token, "citywheels.cancellation", "assist", 10_000, 30, False)
            self.assertEqual(seen.exception.code, code)

    async def test_the_rules_screen_states_the_limit_and_the_defaults(self):
        rules = await self.service.rules(self.supervisor)
        rows = {row["label"]: row for section in rules["sections"] for row in section["rows"]}
        self.assertEqual(rows["Review needed above"]["value"], "100.00 EUR")
        self.assertEqual(rows["Quote lifetime"]["value"], "1 day")
        self.assertEqual(rows["Wait for the write path"]["value"], "3 days")
        self.assertIn("CityWheels cancellation refund", rows["Rule"]["value"])
        self.assertEqual(rows["Audit log"]["value"], "connected")
        self.assertIn("defaults of this build", rules["note"])

    async def test_a_conversation_that_never_opened_a_case_is_listed_and_one_that_did_is_not(self):
        async with build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff), self.queue):
            await self._open()
            listed = await self.service.conversations(self.supervisor)
        self.assertTrue(listed["configured"])
        self.assertEqual([c["correlation"] for c in listed["conversations"]], ["conv-other"])
        self.assertEqual(listed["conversations"][0]["calls"], 1)
        silent = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue)
        self.assertEqual(await silent.conversations(self.supervisor), {"configured": False, "conversations": []})

    async def test_a_case_waiting_for_a_person_still_shows_its_booking(self):
        async with build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff), self.queue):
            case_id = await self.gateway.open_case(self.customer, "CW-1001", storm_claim=True)
            self.addAsyncCleanup(self._stop, case_id)
            for _ in range(100):
                rows = await self.service.cases(self.supervisor)
                if rows and rows[0]["needs"] == "staff":
                    break
                await asyncio.sleep(0.1)
        self.assertEqual((rows[0]["needs"], rows[0]["booking_id"]), ("staff", "CW-1001"))

    async def test_a_rejected_case_says_why_in_a_sentence(self):
        async with build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff), self.queue):
            case_id = await self._open()
            digest = (await self.gateway.quote(self.customer, case_id)).digest()
            await self.gateway.consent(self.customer, case_id, digest)
            await self.service.review(self.supervisor, case_id, "rejected", digest)
            for _ in range(100):
                detail = await self.service.case(self.supervisor, case_id)
                if detail["status"] == "COMPLETED":
                    break
                await asyncio.sleep(0.1)
        self.assertEqual(detail["result"]["code"], "operation_rejected")
        self.assertEqual(detail["reason"]["message"], "A reviewer rejected the quote.")
        self.assertEqual(detail["phase"], "rejected")

    async def test_closed_exceptions_are_kept_for_reading(self):
        tenant = "citywheels"
        self.home.staff.open(tenant, "case-x", None, "staff_required_for_claim", "claims-team", "2026-11-05T10:00:00+00:00")
        self.home.staff.close(tenant, "case-x", "demo-supervisor", "close", "2026-11-05T11:00:00+00:00")
        history = await self.service.staff_history(self.supervisor)
        self.assertEqual([(i["case_id"], i["closed_by"], i["closed_how"]) for i in history], [("case-x", "demo-supervisor", "close")])
        self.assertIn("staff", history[0]["explanation"]["message"].lower())

    async def test_the_cases_of_an_evaluation_run_show_what_failed(self):
        runs = self.root / "runs"
        (runs / "r1").mkdir(parents=True)
        results = [
            {"caseId": "a", "status": "pass", "checks": []}, {"caseId": "a", "status": "pass", "checks": []},
            {"caseId": "b", "status": "pass", "checks": []}, {"caseId": "b", "status": "fail", "checks": [{"id": "answer-0", "status": "fail", "reason": "missing 340"}]},
        ]
        (runs / "r1" / "report.json").write_text(json.dumps({"results": results, "target": {"name": "x help-desk-agent"}}), encoding="utf-8")
        service = CockpitService(self.client, self.home, evals=EvalRuns(runs))
        detail = await service.quality_run(self.supervisor, "r1")
        self.assertEqual([c["case"] for c in detail["cases"]], ["b", "a"])
        self.assertEqual(detail["cases"][0]["failed_checks"], {"answer-0": "missing 340"})
        self.assertEqual((await service.quality_run(self.supervisor, "../etc"))["cases"], [])

    @unittest.skipUnless(HAVE_CLI, "Build a sibling ai-audit checkout")
    async def test_a_decision_of_a_case_replays_and_gives_receipts_and_a_foreign_one_does_not(self):
        real_log = self.root / "real.jsonl"
        evidence = AuditCli(("node", str(CLI)), real_log)
        reader = AuditLogReader(real_log, CLI, "http://127.0.0.1:8793/#token=consoletoken123")
        service = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue, audit=reader)
        async with build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff, evidence), self.queue):
            case_id = await self._open(correlation="conv-real0001")
            for _ in range(100):
                detail = await service.case(self.supervisor, case_id)
                if detail["evidence"]["decisions"]:
                    break
                await asyncio.sleep(0.1)
            decision = detail["evidence"]["decisions"][0]
            self.assertTrue(decision["can_replay"])
            self.assertEqual(decision["link"], f"http://127.0.0.1:8793/#/decisions/{decision['id']}?token=consoletoken123")
            replay = await service.replay(self.supervisor, case_id, decision["id"])
            self.assertTrue(replay["reproduced"] and replay["chain_ok"], replay)
            content, kind, name = await service.receipt(self.supervisor, case_id, decision["id"], "svg")
            self.assertEqual((kind, name.endswith(".svg")), ("image/svg+xml", True))
            self.assertIn(b"<svg", content[:400])
            document, _, _ = await service.receipt(self.supervisor, case_id, decision["id"], "json")
            self.assertEqual(json.loads(document)["decision"]["id"], decision["id"])
            with self.assertRaises(Refusal) as seen:
                await service.receipt(self.supervisor, case_id, decision["id"], "pdf")
            self.assertEqual(seen.exception.code, "receipt_unavailable")
            with self.assertRaises(Refusal) as foreign:
                await service.replay(self.supervisor, case_id, "00000000-0000-0000-0000-000000000000")
            self.assertEqual(foreign.exception.code, "decision_not_in_case")

    async def test_replay_and_receipts_need_the_audit_log(self):
        case_id = None
        async with build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff), self.queue):
            case_id = await self._open()
        silent = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue)
        with self.assertRaises(Refusal) as seen:
            await silent.replay(self.supervisor, case_id, "d1")
        self.assertEqual(seen.exception.code, "audit_unavailable")
        without_cli = CockpitService(self.client, self.home, prefix=self.prefix, task_queue=self.queue, audit=AuditLogReader(self.log))
        with self.assertRaises(Refusal) as nocli:
            await without_cli.replay(self.supervisor, case_id, "d1")
        self.assertEqual(nocli.exception.code, "audit_unavailable")


if __name__ == "__main__":
    unittest.main()
