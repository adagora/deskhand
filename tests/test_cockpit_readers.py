import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cockpit.evidence import AuditLogReader
from cockpit.quality import EvalRuns

TARGET = "CityWheels / help-desk-agent (Python) / OpenAI gpt-4o-mini"
AI_AUDIT_CLI = ROOT.parents[2] / "ai-audit" / "dist" / "src" / "cli.js"


def report(folder: Path, name: str, outcomes: dict[str, list[bool]], target: str = TARGET, created: str = "2026-11-05T14:00:00Z", passed: bool | None = None, suite: str = "CityWheels / help-desk-agent (Python)"):
    results = [
        {"caseId": case, "repetition": index, "status": "pass" if ok else "fail", "checks": []}
        for case, row in outcomes.items() for index, ok in enumerate(row)
    ]
    trials = len(results)
    won = sum(row["status"] == "pass" for row in results)
    data = {
        "createdAt": created, "target": {"name": target}, "suite": {"name": suite},
        "options": {"repetitions": 3}, "results": results, "passed": won == trials if passed is None else passed,
        "summary": {"overall": {"cases": len(outcomes), "trials": trials, "passed": won, "passRate": won / trials, "passRateLowerBound": 0.8, "criticalFailures": 0}},
    }
    (folder / name).mkdir()
    (folder / name / "report.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / name / "dashboard.html").write_text("<html></html>", encoding="utf-8")


class EvalRunsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_flaky_and_failing_cases_are_told_apart(self):
        report(self.root, "run-a", {"steady": [True, True, True], "flaky": [True, False, True], "broken": [False, False, False]})
        run = EvalRuns(self.root).runs()[0]
        self.assertEqual(run["flaky_cases"], ["flaky"])
        self.assertEqual(run["failing_cases"], ["broken"])
        self.assertEqual((run["passed_trials"], run["trials"], run["cases"]), (5, 9, 3))
        self.assertEqual(run["mode"], "live model")
        self.assertEqual(run["human_scoring"], "not recorded")
        self.assertEqual(run["dashboard"], "dashboard.html")

    def test_other_targets_and_derived_runs_are_left_out_and_the_newest_comes_first(self):
        report(self.root, "old", {"a": [True]}, created="2026-11-01T00:00:00Z")
        report(self.root, "new", {"a": [True]}, created="2026-11-05T00:00:00Z")
        report(self.root, "x-injected-regression", {"a": [False]})
        report(self.root, "x-rescored", {"a": [True]})
        report(self.root, "other", {"a": [True]}, target="Some other app / fake", suite="Some other app")
        self.assertEqual([run["run"] for run in EvalRuns(self.root).runs()], ["new", "old"])

    def test_the_verdict_warns_about_flaky_cases_and_a_missing_folder_is_unknown(self):
        report(self.root, "run-a", {"flaky": [True, False, True]}, passed=True)
        self.assertEqual(EvalRuns(self.root).verdict()["state"], "warn")
        self.assertEqual(EvalRuns(self.root / "none").verdict()["state"], "unknown")

    def test_a_dashboard_path_cannot_escape_the_folder(self):
        report(self.root, "run-a", {"a": [True]})
        runs = EvalRuns(self.root)
        self.assertIsNotNone(runs.dashboard_path("run-a", "dashboard.html"))
        for run, name in (("..", "dashboard.html"), ("run-a", "report.json"), ("run-a/..", "dashboard.html"), ("", "dashboard.html"), ("run-a", "../report.json")):
            self.assertIsNone(runs.dashboard_path(run, name), (run, name))

    def test_a_broken_report_is_skipped(self):
        report(self.root, "good", {"a": [True]})
        (self.root / "bad").mkdir()
        (self.root / "bad" / "report.json").write_text("{not json", encoding="utf-8")
        self.assertEqual([run["run"] for run in EvalRuns(self.root).runs()], ["good"])


class AuditLogReaderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "audit.jsonl"

    def test_nothing_matches_an_empty_correlation_id_or_a_missing_log(self):
        self.assertEqual(AuditLogReader(self.log).for_correlation("conv-1")["calls"], [])
        self.log.write_text('{"kind":"llm-call","id":"a","correlation":"conv-1"}\nnot json\n', encoding="utf-8")
        reader = AuditLogReader(self.log)
        self.assertEqual(len(reader.for_correlation("conv-1")["calls"]), 1)
        self.assertEqual(reader.for_correlation("")["calls"], [])
        self.assertEqual(reader.for_correlation("conv-2")["calls"], [])

    def test_without_the_ai_audit_command_the_chain_is_unknown_not_ok(self):
        self.assertEqual(AuditLogReader(self.log).chain()["state"], "unknown")
        self.log.write_text("", encoding="utf-8")
        self.assertEqual(AuditLogReader(self.log, Path("missing.js")).chain()["state"], "unknown")

    @unittest.skipUnless(AI_AUDIT_CLI.exists() and shutil.which("node"), "Build a sibling ai-audit checkout")
    def test_a_real_log_verifies_and_a_changed_one_does_not(self):
        inputs = self.log.parent / "inputs.json"
        inputs.write_text(json.dumps({"rentalMinor": 8000, "bookingFeeMinor": 200, "depositMinor": 30000, "hoursBefore": 30, "cancellable": True, "paidWithVoucher": False}), encoding="utf-8")
        done = subprocess.run(
            ["node", str(AI_AUDIT_CLI), "decide", "--policy", str(ROOT / "policies/refund.json"), "--inputs", str(inputs),
             "--system-id", "t", "--use-case", "cancel-refund", "--correlation", "conv-real", "--log", str(self.log)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        reader = AuditLogReader(self.log, AI_AUDIT_CLI)
        self.assertEqual(reader.chain()["state"], "ok")
        self.assertEqual(len(reader.for_correlation("conv-real")["decisions"]), 1)
        self.log.write_text(self.log.read_text(encoding="utf-8").replace("cancel-refund", "something-else", 1), encoding="utf-8")
        self.assertEqual(reader.chain()["state"], "bad")


if __name__ == "__main__":
    unittest.main()
