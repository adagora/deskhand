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


class ArchitectureTests(unittest.TestCase):
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
