import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from casework.model import Refusal

RECEIPT_FORMATS = {
    "svg": ("svg", "image/svg+xml"),
    "json": ("json", "application/json"),
    "html": ("html", "text/html; charset=utf-8"),
}
TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")


def web_address(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(value)
    return value


def console_link_for(console_url: str | None, route: str) -> str | None:
    if not console_url:
        return None
    base, _, fragment = console_url.partition("#")
    token = TOKEN.search(fragment)
    return f"{base.rstrip('/')}/#/{route}" + (f"?token={token.group(1)}" if token else "")


@dataclass(frozen=True)
class AuditLogReader:
    log: Path
    cli: Path | None = None
    console_url: str | None = None

    def records(self) -> list[dict]:
        if not self.log.exists():
            return []
        found = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    found.append(json.loads(line))
                except ValueError:
                    continue
        return found

    def console_link(self, route: str) -> str | None:
        return console_link_for(self.console_url, route)

    def for_correlation(self, correlation: str) -> dict:
        if not correlation:
            return {"correlation": "", "calls": [], "decisions": []}
        records = self.records()
        calls = [self._call(r) for r in records if r.get("kind") == "llm-call" and r.get("correlation") == correlation]
        decisions = []
        for decision in (r for r in records if r.get("kind") == "decision" and r.get("correlation") == correlation and not str(r.get("id", "")).endswith(":qdl")):
            reviews = [r for r in records if r.get("kind") == "review" and r.get("refId") == decision["id"]]
            effects = [r for r in records if r.get("kind") == "effect" and r.get("refId") == decision["id"]]
            decisions.append(
                {
                    "id": decision["id"], "at": decision.get("ts"), "use_case": decision.get("useCase"),
                    "reviews": [{"id": r["id"], "reviewer": (r.get("review") or {}).get("reviewer"), "verdict": (r.get("review") or {}).get("decision"), "at": r.get("ts")} for r in reviews],
                    "effects": [{"id": r["id"], "action": (r.get("effect") or {}).get("action"), "status": (r.get("effect") or {}).get("status"), "at": r.get("ts")} for r in effects],
                    "receipt": f"ai-audit receipt --log {self.log} --decision {decision['id']} --format svg --output decision.svg",
                    "link": self.console_link(f"decisions/{decision['id']}"),
                    "can_replay": self.cli is not None,
                }
            )
        return {"correlation": correlation, "calls": calls, "decisions": decisions}

    def conversations(self, known: set[str], limit: int = 100) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for record in self.records():
            if record.get("kind") == "llm-call":
                key = record.get("correlation") or ""
                if key not in known:
                    groups.setdefault(key, []).append(record)
        listed = []
        for key, calls in groups.items():
            tools = sorted({tool["name"] for call in calls for tool in (call.get("toolCalls") or []) if isinstance(tool, dict) and tool.get("name")})
            listed.append(
                {
                    "correlation": key,
                    "first_at": calls[0].get("ts"),
                    "last_at": calls[-1].get("ts"),
                    "calls": len(calls),
                    "models": sorted({call.get("model") for call in calls if call.get("model")}),
                    "tools": tools,
                    "actions": sorted({(call.get("decision") or {}).get("action") for call in calls if (call.get("decision") or {}).get("action")}),
                    "errors": sum(1 for call in calls if not isinstance(call.get("upstreamStatus"), int) or call["upstreamStatus"] >= 400),
                    "tokens_in": sum((call.get("usage") or {}).get("input") or 0 for call in calls),
                    "tokens_out": sum((call.get("usage") or {}).get("output") or 0 for call in calls),
                    "link": self.console_link(f"requests/{calls[0]['id']}"),
                }
            )
        return sorted(listed, key=lambda item: item["last_at"] or "", reverse=True)[:limit]

    def replay(self, decision_id: str) -> dict:
        done = self._run("replay", "--decision", decision_id)
        try:
            out = json.loads(done.stdout)
        except ValueError:
            raise Refusal("audit_unavailable") from None
        return {
            "reproduced": bool(out.get("reproduced")),
            "status": out.get("status"),
            "outputs": out.get("outputs"),
            "chain_ok": bool((out.get("chain") or {}).get("ok")),
            "anchor": out.get("anchor"),
        }

    def receipt(self, decision_id: str, format: str) -> tuple[bytes, str, str]:
        if format not in RECEIPT_FORMATS:
            raise Refusal("receipt_unavailable")
        extension, kind = RECEIPT_FORMATS[format]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / f"decision.{extension}"
            options = ["--decision", decision_id, "--output", str(target)] + ([] if format == "html" else ["--format", format])
            done = self._run("receipt", *options)
            if done.returncode != 0 or not target.is_file():
                raise Refusal("receipt_unavailable")
            return target.read_bytes(), kind, f"decision-{decision_id[:8]}.{extension}"

    def chain(self) -> dict:
        if self.cli is None:
            return {"state": "unknown", "detail": "No ai-audit command is configured."}
        if not self.log.exists():
            return {"state": "unknown", "detail": "The audit log does not exist yet."}
        try:
            done = self._run("verify", timeout=30)
        except Refusal:
            return {"state": "unknown", "detail": "The ai-audit command did not run."}
        text = done.stdout.strip()
        if done.returncode == 0 and text.startswith("OK"):
            return {"state": "ok", "detail": text.splitlines()[0]}
        if done.returncode == 1 and text.startswith("BROKEN"):
            return {"state": "bad", "detail": text.splitlines()[0]}
        return {"state": "unknown", "detail": "The ai-audit command did not give a verdict."}

    def last_record_age_minutes(self) -> float | None:
        records = self.records()
        if not records:
            return None
        last = datetime.fromisoformat(records[-1]["ts"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last).total_seconds() / 60

    def _call(self, record: dict) -> dict:
        usage = record.get("usage") or {}
        decision = record.get("decision") or {}
        return {
            "id": record["id"], "at": record.get("ts"), "model": record.get("model"), "status": record.get("upstreamStatus"),
            "action": decision.get("action"), "findings": len(decision.get("findings") or []),
            "tools": [tool["name"] for tool in (record.get("toolCalls") or []) if isinstance(tool, dict) and tool.get("name")],
            "tokens_in": usage.get("input"), "tokens_out": usage.get("output"), "latency_ms": record.get("latencyMs"),
            "link": self.console_link(f"requests/{record['id']}"),
        }

    def _run(self, *arguments: str, timeout: int = 60) -> subprocess.CompletedProcess:
        if self.cli is None:
            raise Refusal("audit_unavailable")
        try:
            return subprocess.run(
                ["node", str(self.cli), *arguments, "--log", str(self.log)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal("audit_unavailable") from None
