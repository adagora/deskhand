import json
from dataclasses import dataclass
from pathlib import Path

DASHBOARD_FILES = ("dashboard.html", "report.html")


@dataclass(frozen=True)
class EvalRuns:
    directory: Path
    target_contains: str = "help-desk-agent"

    def runs(self, limit: int = 12) -> list[dict]:
        if not self.directory.is_dir():
            return []
        found = []
        for folder in self.directory.iterdir():
            report = folder / "report.json"
            if not folder.is_dir() or not report.is_file() or "injected-regression" in folder.name or "rescored" in folder.name:
                continue
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if self.target_contains not in json.dumps(data.get("target", {})) + json.dumps(data.get("suite", {})):
                continue
            found.append(self._summary(folder, data))
        found.sort(key=lambda run: run["created_at"] or "", reverse=True)
        return found[:limit]

    def run_detail(self, run: str) -> list[dict]:
        path = self._inside(run, "report.json")
        if path is None:
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        cases: dict[str, dict] = {}
        for row in data.get("results", []):
            entry = cases.setdefault(row["caseId"], {"case": row["caseId"], "intent": row.get("intent"), "trials": 0, "passed": 0, "failed_checks": {}})
            entry["trials"] += 1
            if row.get("status") == "pass":
                entry["passed"] += 1
            for check in row.get("checks") or []:
                if check.get("status") != "pass":
                    entry["failed_checks"].setdefault(check.get("id", "?"), str(check.get("reason", ""))[:200])
            if row.get("error"):
                entry["failed_checks"].setdefault("error", str(row["error"])[:200])
        return sorted(cases.values(), key=lambda entry: (entry["passed"] / entry["trials"], entry["case"]))

    def _summary(self, folder: Path, data: dict) -> dict:
        overall = (data.get("summary") or {}).get("overall") or {}
        by_case: dict[str, list[bool]] = {}
        for row in data.get("results", []):
            by_case.setdefault(row["caseId"], []).append(row.get("status") == "pass")
        flaky = sorted(case for case, outcomes in by_case.items() if any(outcomes) and not all(outcomes))
        failing = sorted(case for case, outcomes in by_case.items() if not any(outcomes))
        target = (data.get("target") or {}).get("name", "")
        live = "OpenAI" in target or "Gemini" in target
        return {
            "run": folder.name,
            "created_at": data.get("createdAt"),
            "target": target,
            "mode": "live model" if live else "scripted model",
            "repetitions": (data.get("options") or {}).get("repetitions", 1),
            "cases": overall.get("cases"),
            "trials": overall.get("trials"),
            "passed_trials": overall.get("passed"),
            "pass_rate": overall.get("passRate"),
            "pass_rate_lower_bound": overall.get("passRateLowerBound"),
            "critical_failures": overall.get("criticalFailures", 0),
            "flaky_cases": flaky,
            "failing_cases": failing,
            "suite_passed": bool(data.get("passed")),
            "human_scoring": "not recorded",
            "dashboard": next((name for name in DASHBOARD_FILES if (folder / name).is_file()), None),
        }

    def verdict(self) -> dict:
        runs = self.runs(1)
        if not runs:
            return {"state": "unknown", "detail": "No evaluation run was found."}
        latest = runs[0]
        state = "ok" if latest["suite_passed"] and not latest["flaky_cases"] else "warn"
        return {"state": state, "detail": f"{latest['passed_trials']} of {latest['trials']} trials passed in {latest['run']}."}

    def dashboard_path(self, run: str, name: str) -> Path | None:
        if name not in DASHBOARD_FILES:
            return None
        return self._inside(run, name)

    def _inside(self, run: str, name: str) -> Path | None:
        if "/" in run or "\\" in run or run in ("", ".", ".."):
            return None
        path = (self.directory / run / name).resolve()
        if not path.is_file() or self.directory.resolve() not in path.parents:
            return None
        return path
