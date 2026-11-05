import argparse
import asyncio
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from casework.messages import explain
from casework.model import CustomerScope, Refusal
from casework.view import BackendView
from citywheels.agent import run_agent
from citywheels.cli import build_model
from citywheels.fake_model import FakeModel
from citywheels.settings import current
from orchestration.gateway import Gateway
from orchestration.home import activities_for, build_worker, open_home, seed_home
from orchestration.workflow import CancelRefund

DEFAULT_ADDRESS = "localhost:7233"
DEFAULT_HOME = Path("live/casework")


def _emit(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=_plain))


def _plain(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    raise TypeError(type(value).__name__)


def _token() -> str:
    token = os.environ.get("CASEWORK_TOKEN", "")
    if not token:
        raise Refusal("set_CASEWORK_TOKEN")
    return token


def _home(args):
    if not (Path(args.home) / "identities.json").exists():
        raise Refusal("home_not_seeded")
    return open_home(args.home)


async def _gateway(args) -> Gateway:
    home = _home(args)
    try:
        client = await Client.connect(args.address)
    except (RuntimeError, OSError):
        raise Refusal("temporal_unreachable") from None
    return Gateway(client, home.directory)


async def _quote_when_ready(gateway: Gateway, token: str, case_id: str):
    for _ in range(100):
        phase = await gateway.phase(token, case_id)
        if phase != "starting":
            return await gateway.quote(token, case_id), phase
        await asyncio.sleep(0.1)
    return None, "starting"


async def ask_and_open_cases(gateway: Gateway, token: str, question: str, model, backend) -> dict:
    principal = gateway.directory.authenticate(token)
    view = BackendView(backend, CustomerScope(principal.tenant, principal.id))
    correlation = "conv-" + uuid4().hex[:24]
    if hasattr(model, "headers"):
        model.headers["x-audit-correlation"] = correlation
    result = await asyncio.to_thread(run_agent, question, model, view=view)
    cases = []
    for intent in result.write_intents:
        case_id = await gateway.open_case(token, intent["booking_id"], correlation=correlation)
        proposal, phase = await _quote_when_ready(gateway, token, case_id)
        cases.append({
            "case_id": case_id,
            "phase": phase,
            "quote": proposal and {
                "amount_minor": proposal.amount_minor,
                "currency": proposal.currency,
                "digest": proposal.digest(),
                "expires_at": proposal.expires_at,
                "review_required": proposal.review_required,
            },
        })
    return {"answer": result.answer, "correlation": correlation, "cases": cases}


async def demo() -> int:
    with tempfile.TemporaryDirectory() as folder:
        home, tokens = seed_home(Path(folder))
        environment = await WorkflowEnvironment.start_local(dev_server_log_level="error", ui=False)
        try:
            gateway = Gateway(environment.client, home.directory)
            async with build_worker(environment.client, activities_for(home)):
                asked = await ask_and_open_cases(gateway, tokens["customer"], "Please cancel booking CW-1002.", FakeModel(), home.backend)
                case = asked["cases"][0]
                digest = case["quote"]["digest"]
                await gateway.consent(tokens["customer"], case["case_id"], digest)
                await gateway.review(tokens["reviewer"], case["case_id"], "approved", digest)
                result = await gateway.handle_for(tokens["customer"], case["case_id"]).result()
        finally:
            await environment.shutdown()
        report = {"answer": asked["answer"], "quote": case["quote"], "result": result, "refunds": home.backend.refunds()}
        _emit(report)
        done = result.status == "completed" and result.claimed_after_readback and len(report["refunds"]) == 1
        return 0 if done else 1


async def worker(args) -> int:
    client = await Client.connect(args.address)
    async with build_worker(client, activities_for(open_home(args.home))):
        print(json.dumps({"event": "worker_ready", "address": args.address, "home": str(args.home)}), file=sys.stderr)
        await asyncio.Event().wait()
    return 0


async def run_command(args) -> int:
    if args.command == "demo":
        return await demo()
    if args.command == "seed":
        _, tokens = seed_home(args.home)
        _emit({"home": str(args.home), "tokens": tokens})
        return 0
    if args.command == "worker":
        return await worker(args)
    if args.command == "queue":
        token = _token()
        home = _home(args)
        principal = home.directory.authenticate(token)
        if "operator" not in principal.roles:
            raise Refusal("forbidden")
        _emit([dataclasses.asdict(item) for item in home.staff.items(principal.tenant, args.status)])
        return 0
    token = _token()
    gateway = await _gateway(args)
    if args.command == "ask":
        _emit(await ask_and_open_cases(gateway, token, args.question, build_model(current(args.provider)), _home(args).backend))
    elif args.command == "status":
        handle = gateway.handle_for(token, args.case)
        _emit({
            "phase": await gateway.phase(token, args.case),
            "proposal": await gateway.quote(token, args.case),
            "staff_code": await handle.query(CancelRefund.current_staff_code),
            "resolution_refusal": await handle.query(CancelRefund.latest_resolution_refusal),
        })
    elif args.command == "consent":
        await gateway.consent(token, args.case, args.digest)
    elif args.command == "review":
        await gateway.review(token, args.case, args.verdict, args.digest)
    elif args.command == "resolve":
        await gateway.resolve(token, args.case, args.kind)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m orchestration", description="Run CityWheels cases on Temporal.")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run one whole case on a throwaway server and print the result.")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--home", type=Path, default=DEFAULT_HOME)
        command.add_argument("--address", default=DEFAULT_ADDRESS)
        return command

    add("seed", "Create the fake backend, one active grant and demo identities. Prints tokens once.")
    add("worker", "Run the workflow and activities until stopped.")
    ask = add("ask", "Answer a question. A clear request opens a case and prints its quote.")
    ask.add_argument("question")
    ask.add_argument("--provider", choices=("fake", "openai", "gemini"), default=None)
    add("status", "Show a case.").add_argument("--case", required=True)
    consent = add("consent", "Confirm the exact quote as its customer.")
    consent.add_argument("--case", required=True)
    consent.add_argument("--digest", required=True)
    review = add("review", "Approve or reject the exact quote as a reviewer.")
    review.add_argument("--case", required=True)
    review.add_argument("--digest", required=True)
    review.add_argument("--verdict", choices=("approved", "rejected"), required=True)
    resolve = add("resolve", "Close an owned exception, or complete its refund as a supervisor.")
    resolve.add_argument("--case", required=True)
    resolve.add_argument("--kind", choices=("complete-refund", "close"), required=True)
    add("queue", "List staff items.").add_argument("--status", choices=("open", "closed"), default="open")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return asyncio.run(run_command(args))
    except Refusal as refusal:
        print(json.dumps({"ok": False, **explain(refusal.code)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
