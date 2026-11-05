import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from casework.model import Refusal
from cockpit.evidence import web_address

BUILD = Path("dist") / "src" / "cli.js"
PROXY_ALLOW = Path(__file__).resolve().parents[1] / "policies" / "proxy-allow.json"
READY_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 5.0


def build_of(folder: Path, code: str) -> Path:
    cli = Path(folder) / BUILD
    if not cli.is_file():
        raise Refusal(code)
    return cli


def console_command(cli: Path, log: Path, port: int, *, config: Path | None = None, metrics_url: str | None = None, reviewer: str | None = None) -> list[str]:
    command = ["node", str(cli), "console", "--log", str(log), "--port", str(port)]
    if config:
        command += ["--config", str(config)]
    if metrics_url:
        command += ["--metrics", metrics_url]
    if reviewer:
        command += ["--reviewer", reviewer]
    return command


def dashboard_command(cli: Path, runs: Path, port: int) -> list[str]:
    return ["node", str(cli), "dashboard", str(runs), "--port", str(port)]


def proxy_command(cli: Path, log: Path, port: int, config: Path, upstream: str) -> list[str]:
    return [
        "node", str(cli), "serve", "--mode", "redact", "--log", str(log), "--port", str(port),
        "--config", str(config), "--upstream-openai", upstream, "--allow-hashes", str(PROXY_ALLOW),
    ]


def helper_environment(**extra: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), **extra}


def _event(line: str) -> dict | None:
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class Helper:
    def __init__(self, name: str, command: list[str], environment: dict[str, str]):
        self.name = name
        self.command = command
        self.events: queue.Queue = queue.Queue()
        self.process = subprocess.Popen(
            command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._readers = [threading.Thread(target=self._pump, args=(stream,), daemon=True) for stream in (self.process.stdout, self.process.stderr)]
        for reader in self._readers:
            reader.start()

    def _pump(self, stream) -> None:
        for line in stream:
            event = _event(line)
            if event is not None:
                self.events.put(event)

    def wait_for(self, marker: str, timeout: float = READY_TIMEOUT_S) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            try:
                event = self.events.get(timeout=max(min(0.25, remaining), 0.01))
            except queue.Empty:
                if remaining <= 0:
                    raise Refusal("helper_did_not_start") from None
                if self.process.poll() is not None:
                    return self._after_exit(marker)
                continue
            if marker in json.dumps(event):
                return event

    def _after_exit(self, marker: str) -> dict:
        for reader in self._readers:
            reader.join(timeout=1.0)
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                raise Refusal("helper_did_not_start") from None
            if marker in json.dumps(event):
                return event

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m cockpit.stack",
        description="Start the operator side of Deskhand with one command: Temporal, the worker, the cockpit, the Auditline console and the IntentGauge dashboard.",
    )
    root.add_argument("--auditline", type=Path, required=True, help="Folder of a built Auditline checkout.")
    root.add_argument("--intentgauge", type=Path, help="Folder of a built IntentGauge checkout. Without it the dashboard is not started.")
    root.add_argument("--runs", type=Path, help="IntentGauge runs folder. Default: runs in the IntentGauge folder.")
    root.add_argument("--home", type=Path, default=Path("live/casework"), help="Case home. It is seeded on the first start.")
    root.add_argument("--audit-log", type=Path, help="Audit log. Default: audit.jsonl in the case home.")
    root.add_argument("--address", help="Use this Temporal server. Without it a local development server starts.")
    root.add_argument("--proxy-config", type=Path, help="Start the Auditline proxy with this gateway.json. It needs OPENAI_API_KEY in the environment.")
    root.add_argument("--upstream-openai", type=web_address, default="https://api.openai.com", help="Provider address for the proxy.")
    root.add_argument("--console-reviewer", help="Reviewer name for the console. Without it the console only reads.")
    root.add_argument("--cockpit-port", type=int, default=8799)
    root.add_argument("--console-port", type=int, default=8793)
    root.add_argument("--dashboard-port", type=int, default=4317)
    root.add_argument("--proxy-port", type=int, default=8787)
    return root
