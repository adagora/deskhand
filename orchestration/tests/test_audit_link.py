import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from casework.backend import booking_from_shop_record
from casework.evidence import DEFAULT_POLICY, AuditCli
from casework.model import CustomerScope, Refusal, Timing
from casework.rule import refund_minor
from citywheels.agent import run_agent
from citywheels.bookings import load_bookings
from citywheels.clock import FIXED_NOW
from citywheels.messages import STAFF_EMAIL, STAFF_PHONE
from citywheels.openai_model import OpenAIModel
from orchestration.activities import CaseActivities
from orchestration.gateway import Gateway
from orchestration.home import build_worker, seed_home
from orchestration.workflow import CancelRefund

ROOT = Path(__file__).resolve().parents[2]
AI_AUDIT = Path(os.environ.get("AI_AUDIT_ROOT", ROOT.parents[2] / "ai-audit"))
CLI = AI_AUDIT / "dist/src/cli.js"
PROXY_ALLOW = ROOT / "policies/proxy-allow.json"
AVAILABLE = CLI.exists() and shutil.which("node") is not None
FAST = Timing(read_deadline_s=5, write_deadline_s=5, attempt_timeout_s=3, first_retry_s=0.05, max_retry_s=0.2)
SCOPE = CustomerScope("citywheels", "demo-customer")


def cli(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(CLI), *arguments], capture_output=True, text=True, timeout=60, check=False)


def log_records(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


@unittest.skipUnless(AVAILABLE, "Build a sibling ai-audit checkout or set AI_AUDIT_ROOT")
class RuleConformanceTests(unittest.TestCase):
    def test_the_qdl_rule_and_the_money_rule_agree_on_every_confirmed_booking(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = AuditCli(("node", str(CLI)), Path(folder) / "audit.jsonl")
            home, _ = seed_home(Path(folder) / "home")
            desk = home.desk(business_clock=lambda: FIXED_NOW, release=lambda: "release")
            checked = 0
            for record in load_bookings():
                if record["status"] != "confirmed":
                    continue
                booking = booking_from_shop_record(record, SCOPE.tenant, SCOPE.customer)
                proposal = desk.propose(SCOPE, "case-1", record["id"])
                self.assertEqual(proposal.amount_minor, refund_minor(booking, FIXED_NOW))
                self.assertIsNotNone(evidence.decide(proposal, booking).id, record["id"])
                checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_the_qdl_rule_rounds_half_a_cent_up_like_the_money_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = AuditCli(("node", str(CLI)), Path(folder) / "audit.jsonl")
            home, _ = seed_home(Path(folder) / "home")
            desk = home.desk(business_clock=lambda: FIXED_NOW, release=lambda: "release")
            record = next(item for item in load_bookings() if item["id"] == "CW-1002")
            for index, rental in enumerate((25.01, 10.03, 0.01, 119.99, 1.05)):
                odd = {**record, "id": f"CW-ODD{index}", "rental_price_eur": rental}
                booking = booking_from_shop_record(odd, SCOPE.tenant, SCOPE.customer)
                home.backend.seed([booking])
                proposal = desk.propose(SCOPE, f"case-odd-{index}", booking.id)
                self.assertEqual(proposal.amount_minor, refund_minor(booking, FIXED_NOW))
                self.assertIsNotNone(evidence.decide(proposal, booking).id, rental)

    def test_a_changed_qdl_rule_is_refused_as_a_disagreement(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
            for item in policy["inputs"]:
                if item["id"] == "percentHalf":
                    item["value"] = 25
            changed = Path(folder) / "changed.json"
            changed.write_text(json.dumps(policy), encoding="utf-8")
            evidence = AuditCli(("node", str(CLI)), Path(folder) / "audit.jsonl", policy=changed)
            home, _ = seed_home(Path(folder) / "home")
            desk = home.desk(business_clock=lambda: FIXED_NOW, release=lambda: "release")
            proposal = desk.propose(SCOPE, "case-1", "CW-1002")
            with self.assertRaises(Refusal) as seen:
                evidence.decide(proposal, home.backend.owned(SCOPE, "CW-1002"))
        self.assertEqual(seen.exception.code, "rule_disagreement")


@unittest.skipUnless(AVAILABLE, "Build a sibling ai-audit checkout or set AI_AUDIT_ROOT")
class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = await WorkflowEnvironment.start_local(dev_server_log_level="error", ui=False)
        self.addAsyncCleanup(self.env.shutdown)
        self.home, self.tokens = seed_home(self.root / "home")
        self.log = self.root / "audit.jsonl"
        self.client = await Client.connect(self.env.client.service_client.config.target_host)
        self.queue = f"casework-{uuid4().hex}"
        self.gateway = Gateway(self.client, self.home.directory, self.queue, f"case-{uuid4().hex}")

    def _worker(self, evidence):
        return build_worker(self.client, CaseActivities(self.home.desk(), self.home.staff, evidence), self.queue)

    async def _wait(self, handle, *phases):
        for _ in range(400):
            phase = await handle.query(CancelRefund.current_phase)
            if phase in phases:
                return phase
            await asyncio.sleep(0.05)
        raise AssertionError(f"last phase {phase}, wanted {phases}")

    async def test_a_completed_case_leaves_a_decision_review_and_effect_in_the_audit_log(self):
        async with self._worker(AuditCli(("node", str(CLI)), self.log)):
            case_id = await self.gateway.open_case(self.tokens["customer"], "CW-1002", timing=FAST)
            handle = self.gateway.handle_for(self.tokens["customer"], case_id)
            await self._wait(handle, "waiting-for-consent")
            proposal = await handle.query(CancelRefund.current_proposal)
            await self.gateway.consent(self.tokens["customer"], case_id, proposal.digest())
            await self.gateway.review(self.tokens["reviewer"], case_id, "approved", proposal.digest())
            result = await asyncio.wait_for(handle.result(), timeout=30)
        self.assertEqual((result.status, result.evidence), ("completed", "recorded"))
        records = log_records(self.log)
        decision = next(record for record in records if record["kind"] == "decision")
        review = next(record for record in records if record["kind"] == "review")
        effect = next(record for record in records if record["kind"] == "effect")
        self.assertEqual(review["review"]["decision"], "approved")
        self.assertEqual(review["review"]["reviewer"], "demo-reviewer")
        self.assertEqual(effect["effect"]["status"], "done")
        self.assertEqual(effect["effect"]["action"], "quote-refund-to-card")
        self.assertEqual(effect["effect"]["review"], review["id"])
        self.assertEqual(cli("verify", "--log", str(self.log)).returncode, 0)
        self.assertEqual(cli("replay", "--decision", decision["id"], "--log", str(self.log)).returncode, 0)
        self.assertEqual(cli("await", "--decision", decision["id"], "--timeout-s", "0", "--log", str(self.log)).returncode, 0)

    async def test_no_write_happens_while_the_evidence_cannot_be_kept(self):
        broken = AuditCli(("node", str(self.root / "missing.js")), self.log)
        async with self._worker(broken):
            case_id = await self.gateway.open_case(self.tokens["customer"], "CW-1002", timing=FAST)
            handle = self.gateway.handle_for(self.tokens["customer"], case_id)
            await self._wait(handle, "owned-exception")
            self.assertEqual(await handle.query(CancelRefund.current_staff_code), "evidence_unavailable")
            self.assertEqual(self.home.backend.refund_count(), 0)
            self.assertEqual(self.home.backend.owned(SCOPE, "CW-1002").status, "confirmed")
            await self.gateway.resolve(self.tokens["supervisor"], case_id, "close")
            self.assertEqual((await asyncio.wait_for(handle.result(), timeout=30)).status, "closed_by_staff")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _FakeProvider(BaseHTTPRequestHandler):
    seen: list[dict] = []
    authorization: list[str] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append(body)
        type(self).authorization.append(self.headers.get("authorization", ""))
        first_turn = not any(message["role"] == "tool" for message in body["messages"])
        message = (
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "calculate_refund", "arguments": json.dumps({"booking_id": "CW-1002"})},
            }]}
            if first_turn
            else {"role": "assistant", "content": f"You get 340 EUR back. Contact {STAFF_EMAIL}."}
        )
        reply = json.dumps({
            "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        return None


@unittest.skipUnless(AVAILABLE, "Build a sibling ai-audit checkout or set AI_AUDIT_ROOT")
class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "audit.jsonl"
        _FakeProvider.seen, _FakeProvider.authorization = [], []
        self.provider = HTTPServer(("127.0.0.1", 0), _FakeProvider)
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        self.addCleanup(self.provider.server_close)
        self.addCleanup(self.provider.shutdown)
        self.port = _free_port()
        self.proxy = subprocess.Popen(
            [
                "node", str(CLI), "serve", "--mode", "redact", "--log", str(self.log), "--port", str(self.port),
                "--upstream-openai", f"http://127.0.0.1:{self.provider.server_port}", "--allow-hashes", str(PROXY_ALLOW),
            ],
            env={
                "PATH": os.environ["PATH"],
                "AI_AUDIT_UPSTREAM_OPENAI_KEY": "sk-fake-upstream",
                "AI_AUDIT_CLIENT_KEY": "aia-fake-client",
            },
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
        )
        self.addCleanup(self.proxy.stderr.close)
        self.addCleanup(self.proxy.wait)
        self.addCleanup(self.proxy.kill)
        for line in self.proxy.stderr:
            if '"listening"' in line:
                break

    def test_the_agent_reaches_the_model_through_the_proxy_without_a_code_change(self):
        model = OpenAIModel("aia-fake-client", "gpt-4o-mini", f"http://127.0.0.1:{self.port}/v1")
        result = run_agent("My mail is ada@example.com. What do I get back for CW-1002?", model)
        self.assertIn("340 EUR", result.answer)
        self.assertEqual([step["name"] for step in result.tool_trace], ["calculate_refund"])
        self.assertEqual(set(_FakeProvider.authorization), {"Bearer sk-fake-upstream"})
        sent = json.dumps(_FakeProvider.seen)
        self.assertNotIn("ada@example.com", sent)
        self.assertIn("[REDACTED:pii-email]", sent)
        self.assertIn(STAFF_EMAIL, sent)
        self.assertIn(STAFF_PHONE, sent)
        self.assertEqual(self._wait_for_calls(2), 2)
        self.assertEqual(cli("verify", "--log", str(self.log)).returncode, 0)
        self.assertNotIn("ada@example.com", self.log.read_text(encoding="utf-8"))

    def _wait_for_calls(self, count: int) -> int:
        found = 0
        for _ in range(100):
            found = sum(record["kind"] == "llm-call" for record in log_records(self.log))
            if found >= count:
                break
            time.sleep(0.05)
        return found

    def test_the_allow_list_holds_only_the_public_shop_contacts(self):
        expected = {hashlib.sha256(value.encode()).hexdigest() for value in (STAFF_EMAIL, STAFF_PHONE)}
        self.assertEqual(set(json.loads(PROXY_ALLOW.read_text(encoding="utf-8"))), expected)


if __name__ == "__main__":
    unittest.main()
