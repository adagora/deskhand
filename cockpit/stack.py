import asyncio
import json
import os
import signal
import sys
import threading

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from casework.messages import explain
from casework.model import Refusal
from cockpit.evidence import AuditLogReader
from cockpit.launch import Helper, build_of, console_command, dashboard_command, helper_environment, parser, proxy_command
from cockpit.quality import EvalRuns
from cockpit.server import CockpitServer
from cockpit.service import CockpitService
from orchestration.home import activities_for, build_worker, open_home, seed_home


def _say(value: dict) -> None:
    print(json.dumps(value), file=sys.stderr, flush=True)


async def _client(address: str | None):
    if address:
        try:
            return await Client.connect(address), None
        except (RuntimeError, OSError):
            raise Refusal("temporal_unreachable") from None
    try:
        environment = await WorkflowEnvironment.start_local(dev_server_log_level="error", ui=False)
    except Exception:
        raise Refusal("temporal_unreachable") from None
    return environment.client, environment


async def run(args) -> int:
    auditline = build_of(args.auditline, "auditline_not_built")
    intentgauge = build_of(args.intentgauge, "intentgauge_not_built") if args.intentgauge else None
    runs = args.runs or (args.intentgauge / "runs" if args.intentgauge else None)
    helpers: list[Helper] = []
    environment = None
    server = None
    try:
        seeded = None
        if (args.home / "identities.json").exists():
            home = open_home(args.home)
        else:
            home, seeded = seed_home(args.home)
        log = args.audit_log or args.home / "audit.jsonl"
        os.environ["AUDITLINE_CLI"], os.environ["AUDITLINE_LOG"] = str(auditline), str(log)

        client, environment = await _client(args.address)

        proxy_url = None
        if args.proxy_config:
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                raise Refusal("provider_key_missing")
            proxy = Helper("proxy", proxy_command(auditline, log, args.proxy_port, args.proxy_config, args.upstream_openai), helper_environment(AI_AUDIT_UPSTREAM_OPENAI_KEY=key))
            helpers.append(proxy)
            proxy_url = proxy.wait_for('"listening"')["url"]

        console = Helper(
            "console",
            console_command(auditline, log, args.console_port, config=args.proxy_config, metrics_url=f"{proxy_url}/metrics" if proxy_url else None, reviewer=args.console_reviewer),
            helper_environment(),
        )
        helpers.append(console)
        console_url = console.wait_for('"url"')["url"]

        dashboard_url = None
        if intentgauge:
            dashboard = Helper("dashboard", dashboard_command(intentgauge, runs, args.dashboard_port), helper_environment())
            helpers.append(dashboard)
            dashboard_url = dashboard.wait_for('"dashboard"')["dashboard"]

        service = CockpitService(
            client, home, audit=AuditLogReader(log, auditline, console_url), evals=EvalRuns(runs) if runs else None,
            proxy_url=proxy_url, console_url=console_url, evaluation_url=dashboard_url,
        )
        candidate = CockpitServer(service, asyncio.get_running_loop(), "127.0.0.1", args.cockpit_port)
        threading.Thread(target=candidate.serve_forever, daemon=True).start()
        server = candidate

        stop = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(name, stop.set)
        cockpit_url = f"http://127.0.0.1:{server.server_address[1]}/"
        async with build_worker(client, activities_for(home)):
            _say({
                "event": "stack", "cockpit": cockpit_url + (f"#token={seeded['supervisor']}" if seeded else ""),
                "console": console_url, "evaluation": dashboard_url, "proxy": proxy_url, "home": str(args.home),
                **({"tokens": seeded} if seeded else {}),
            })
            await stop.wait()
        return 0
    finally:
        if server:
            server.shutdown()
            server.server_close()
        for helper in reversed(helpers):
            helper.stop()
        if environment:
            await environment.shutdown()


def main() -> int:
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Refusal as error:
        _say({"ok": False, "code": error.code, **explain(error.code)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
