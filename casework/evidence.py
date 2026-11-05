import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from casework.backend import Booking
from casework.model import DecisionRef, Proposal, Refusal, Review

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = _ROOT / "policies" / "refund.json"
ALREADY_REVIEWED = "audit_already_reviewed"


class EvidenceUnavailable(RuntimeError):
    pass


class NoEvidence:
    def decide(self, proposal: Proposal, booking: Booking) -> DecisionRef:
        return DecisionRef()

    def review(self, decision: DecisionRef, review: Review) -> str | None:
        return None

    def effect(self, decision: DecisionRef, status: str, review_id: str | None, code: str | None) -> None:
        return None


def refund_rule_inputs(proposal: Proposal, booking: Booking) -> dict:
    start = datetime.fromisoformat(booking.starts_at)
    business_at = datetime.fromisoformat(proposal.business_at)
    return {
        "rentalMinor": booking.rental_minor,
        "bookingFeeMinor": booking.booking_fee_minor,
        "depositMinor": booking.deposit_minor,
        "hoursBefore": (start - business_at).total_seconds() / 3600,
        "cancellable": booking.status == "confirmed",
        "paidWithVoucher": booking.payment_kind == "voucher",
    }


def _proposed_action(outputs: list) -> dict | None:
    actions = [item for item in outputs if isinstance(item, dict) and item.get("status") == "simulated"]
    return actions[0] if len(actions) == 1 else None


@dataclass(frozen=True)
class AuditCli:
    command: tuple[str, ...]
    log: Path
    policy: Path = DEFAULT_POLICY
    system_id: str = "citywheels"
    use_case: str = "cancel-refund"

    def decide(self, proposal: Proposal, booking: Booking) -> DecisionRef:
        with tempfile.TemporaryDirectory() as folder:
            inputs = Path(folder) / "inputs.json"
            inputs.write_text(json.dumps(refund_rule_inputs(proposal, booking)), encoding="utf-8")
            reply = self._run(
                "decide", "--policy", str(self.policy), "--inputs", str(inputs),
                "--system-id", self.system_id, "--use-case", self.use_case,
            )
        action = _proposed_action(reply.get("outputs") or [])
        if reply.get("status") != "completed" or action is None:
            raise Refusal("rule_disagreement")
        if (Decimal(str(action["payload"])) * 100).to_integral_value() != proposal.amount_minor:
            raise Refusal("rule_disagreement")
        return DecisionRef(id=reply["decision"], action=action["action"])

    def review(self, decision: DecisionRef, review: Review) -> str | None:
        try:
            reply = self._run(
                "approve", "--decision", decision.id, "--reviewer", review.reviewer, "--verdict", review.verdict
            )
        except EvidenceUnavailable as error:
            if ALREADY_REVIEWED in str(error):
                return None
            raise
        return reply["review"]

    def effect(self, decision: DecisionRef, status: str, review_id: str | None, code: str | None) -> None:
        arguments = ["effect", "--decision", decision.id, "--action", decision.action, "--status", status]
        if review_id:
            arguments += ["--review", review_id]
        if code:
            arguments += ["--code", code]
        self._run(*arguments)

    def _run(self, *arguments: str) -> dict:
        completed = subprocess.run(
            [*self.command, *arguments, "--log", str(self.log)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if completed.returncode not in (0, 1) or not completed.stdout.strip():
            raise EvidenceUnavailable(completed.stderr.strip() or f"exit {completed.returncode}")
        return json.loads(completed.stdout)


def evidence_from_environment(environment=os.environ):
    cli, log = environment.get("AI_AUDIT_CLI"), environment.get("AI_AUDIT_LOG")
    if not cli or not log:
        return NoEvidence()
    return AuditCli(command=("node", cli), log=Path(log))
