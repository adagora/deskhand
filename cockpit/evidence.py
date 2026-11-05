import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RECORD_KINDS = ("llm-call", "decision", "review", "effect")


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

    def for_correlation(self, correlation: str) -> dict:
        if not correlation:
            return {"correlation": "", "calls": [], "decisions": []}
        records = self.records()
        calls = [
            {"id": r["id"], "at": r.get("ts"), "model": r.get("model"), "status": r.get("upstreamStatus"),
             "action": (r.get("decision") or {}).get("action")}
            for r in records if r.get("kind") == "llm-call" and r.get("correlation") == correlation
        ]
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
                }
            )
        return {"correlation": correlation, "calls": calls, "decisions": decisions, "console_url": self.console_url}

    def chain(self) -> dict:
        if self.cli is None:
            return {"state": "unknown", "detail": "No ai-audit command is configured."}
        if not self.log.exists():
            return {"state": "unknown", "detail": "The audit log does not exist yet."}
        try:
            done = subprocess.run(
                ["node", str(self.cli), "verify", "--log", str(self.log)],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
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
