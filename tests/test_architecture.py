"""Import direction. High-level modules must not reach files, HTTP, or adapters."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "citywheels"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HIGH_LEVEL = {
    "clock.py",
    "refunds.py",
    "schedule.py",
    "messages.py",
    "model.py",
    "prompts.py",
}
IO_MODULES = {
    "bookings",
    "policies",
    "paths",
    "dotenv",
    "cases",
    "briefing",
    "tools",
    "agent",
    "cli",
    "fake_model",
    "openai_model",
    "gemini_model",
}
IO_STDLIB = {"urllib", "socket", "http"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _graph() -> dict[str, set[str]]:
    graph = {}
    for path in PACKAGE.glob("*.py"):
        deps = set()
        for module in _imports(path):
            if module == "citywheels" or module.startswith("citywheels."):
                name = module.split(".")[1] if "." in module else ""
                if name:
                    deps.add(name)
        graph[path.stem] = deps
    return graph


def _sources(package: str) -> list[Path]:
    return [path for path in (ROOT / package).glob("*.py")]


class ArchitectureTests(unittest.TestCase):
    def test_the_agent_does_not_know_about_casework_or_temporal(self):
        for path in PACKAGE.glob("*.py"):
            for module in _imports(path):
                top = module.split(".")[0]
                self.assertNotIn(top, {"casework", "orchestration", "temporalio"}, f"{path.name} imports {module}")

    def test_casework_stays_on_the_standard_library_and_the_money_rule(self):
        for path in _sources("casework"):
            for module in _imports(path):
                top = module.split(".")[0]
                self.assertNotIn(top, {"temporalio", "orchestration"}, f"{path.name} imports {module}")

    def test_nothing_imports_the_cockpit(self):
        for package in ("citywheels", "casework", "orchestration"):
            for path in _sources(package):
                for module in _imports(path):
                    self.assertNotEqual(module.split(".")[0], "cockpit", f"{path.name} imports {module}")

    def test_the_cockpit_server_and_readers_do_not_touch_temporal_or_the_agent(self):
        for name in ("server.py", "evidence.py", "quality.py"):
            for module in _imports(ROOT / "cockpit" / name):
                self.assertNotIn(module.split(".")[0], {"temporalio", "citywheels", "orchestration"}, f"{name} imports {module}")

    def test_the_cockpit_page_builds_no_markup_from_data(self):
        script = (ROOT / "cockpit" / "static" / "cockpit.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(forbidden, script, forbidden)
        page = (ROOT / "cockpit" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn('lang="en"', page)

    def test_the_sidebar_links_to_other_tools_open_safely(self):
        script = (ROOT / "cockpit" / "static" / "cockpit.js").read_text(encoding="utf-8")
        start = script.index("function renderTools()")
        body = script[start:script.index("\n}\n", start)]
        self.assertIn('target: "_blank"', body)
        self.assertIn('rel: "noopener noreferrer"', body)
        self.assertIn("https?:", body)

    def test_casework_has_one_money_rule(self):
        for path in _sources("casework"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("timedelta(hours=48)", text, path.name)
            self.assertNotIn("timedelta(hours=24)", text, path.name)
        self.assertIn("refund_for_booking", (ROOT / "casework" / "rule.py").read_text(encoding="utf-8"))

    def test_the_workflow_imports_only_temporal_and_the_pure_model(self):
        allowed = {"asyncio", "datetime", "temporalio", "casework.model"}
        path = ROOT / "orchestration" / "workflow.py"
        for module in _imports(path):
            self.assertTrue(
                module in allowed or module.split(".")[0] in {"temporalio", "datetime", "asyncio"},
                f"workflow.py imports {module}",
            )

    def test_the_workflow_reads_no_clock_of_its_own_and_sets_no_short_deadline(self):
        text = (ROOT / "orchestration" / "workflow.py").read_text(encoding="utf-8")
        gateway = (ROOT / "orchestration" / "gateway.py").read_text(encoding="utf-8")
        self.assertNotIn("datetime.now", text)
        self.assertNotIn("time.time", text)
        self.assertNotIn("execution_timeout", gateway)

    def test_high_level_modules_do_not_touch_io(self):
        for name in HIGH_LEVEL:
            for module in _imports(PACKAGE / name):
                top = module.split(".")[0]
                city = module.split(".")[1] if module.startswith("citywheels.") and "." in module else ""
                self.assertNotIn(top, IO_STDLIB, f"{name} imports {module}")
                self.assertNotIn(city, IO_MODULES, f"{name} imports {module}")

    def test_answer_and_eval_call_the_change_rule(self):
        for relative in ("citywheels/fake_model.py", "eval/run_eval.py"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("timedelta(hours=24)", text, relative)
            self.assertIn("change_allowed", text, relative)

    def test_prompt_does_not_hardcode_the_season(self):
        text = (PACKAGE / "prompts.py").read_text(encoding="utf-8")
        self.assertNotIn("April to October", text)
        self.assertIn("applicable_hours", text)

    def test_no_import_cycles(self):
        graph = _graph()
        seen = set()
        stack = []

        def walk(name: str) -> None:
            if name in stack:
                cycle = stack[stack.index(name) :] + [name]
                self.fail("import cycle: " + " -> ".join(cycle))
            if name in seen or name not in graph:
                return
            stack.append(name)
            for dep in graph[name]:
                walk(dep)
            stack.pop()
            seen.add(name)

        for name in graph:
            walk(name)


if __name__ == "__main__":
    unittest.main()
