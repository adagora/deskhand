import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from temporalio.client import Client

from casework.messages import explain
from cockpit.evidence import AuditLogReader
from cockpit.quality import EvalRuns
from cockpit.server import CockpitServer
from cockpit.service import CockpitService
from orchestration.home import open_home

DEFAULT_HOME = Path("live/casework")
DEFAULT_ADDRESS = "localhost:7233"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m cockpit", description="The operator cockpit for CityWheels cases.")
    root.add_argument("--home", type=Path, default=DEFAULT_HOME, help="Folder made by `python -m orchestration seed`.")
    root.add_argument("--address", default=DEFAULT_ADDRESS, help="Temporal server address.")
    root.add_argument("--port", type=int, default=8799)
    root.add_argument("--audit-log", type=Path, default=os.environ.get("AI_AUDIT_LOG") and Path(os.environ["AI_AUDIT_LOG"]), help="ai-audit log to join by correlation id.")
    root.add_argument("--audit-cli", type=Path, default=os.environ.get("AI_AUDIT_CLI") and Path(os.environ["AI_AUDIT_CLI"]), help="dist/src/cli.js of an ai-audit build, used to verify the chain.")
    root.add_argument("--console-url", help="Address of the ai-audit console, linked from a case.")
    root.add_argument("--proxy-url", help="Address of the ai-audit proxy, checked on the Health screen.")
    root.add_argument("--evals-dir", type=Path, help="IntentGauge runs folder to read evaluation reports from.")
    return root


def fail(code: str) -> int:
    print(json.dumps({"ok": False, **explain(code)}), file=sys.stderr)
    return 1


def main() -> int:
    args = parser().parse_args()
    if not (args.home / "identities.json").exists():
        return fail("home_not_seeded")
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    try:
        client = asyncio.run_coroutine_threadsafe(Client.connect(args.address), loop).result(timeout=15)
    except Exception:
        return fail("temporal_unreachable")
    audit = AuditLogReader(args.audit_log, args.audit_cli, args.console_url) if args.audit_log else None
    evals = EvalRuns(args.evals_dir) if args.evals_dir else None
    service = CockpitService(client, open_home(args.home), audit=audit, evals=evals, proxy_url=args.proxy_url)
    server = CockpitServer(service, loop, "127.0.0.1", args.port)
    token = os.environ.get("CASEWORK_TOKEN", "")
    url = f"http://127.0.0.1:{server.server_address[1]}/" + (f"#token={token}" if token else "")
    print(json.dumps({"event": "cockpit", "url": url, "tenant_home": str(args.home)}), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
