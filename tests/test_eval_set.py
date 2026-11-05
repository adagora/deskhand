import importlib.util
import json
import re
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

README_EXAMPLES = [
    "What time do you open on Saturday?",
    "How much is an e-bike for a whole day?",
    "I want to cancel booking CW-1002. How much will I get back?",
    "Can my 14-year-old rent an e-bike?",
    "What is the capital of Australia?",
]


def _load_eval():
    path = ROOT / "eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("citywheels_run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalSetTests(unittest.TestCase):
    def test_questions_cover_the_readme_examples(self):
        questions = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(questions), 10)
        asked = [item["question"] for item in questions]
        for example in README_EXAMPLES:
            self.assertIn(example, asked)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.split("## Example questions", 1)[1]
        listed = re.findall(r"(?m)^- (.+)$", start.split("## ", 1)[0])
        self.assertEqual(listed, README_EXAMPLES)
        refunds = [item for item in questions if item["kind"] == "refund"]
        refusals = [item for item in questions if item["kind"] == "refuse"]
        self.assertGreaterEqual(len(refunds), 2)
        self.assertGreaterEqual(len(refusals), 2)
        for item in questions:
            self.assertIn("question", item)
            self.assertIn("kind", item)
            self.assertTrue(item["id"])

    def test_env_example_has_names_only(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("OPENAI_API_KEY=", text)
        self.assertIn("GEMINI_API_KEY=", text)
        self.assertIn("CITYWHEELS_PROVIDER=openai", text)
        self.assertNotIn("CITYWHEELS_API_KEY", text)
        self.assertNotRegex(text, r"sk-[A-Za-z0-9]")
        self.assertNotRegex(text, r"AKIA[0-9A-Z]{16}")
        for line in text.splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            self.assertTrue(name)
            self.assertNotIn("secret", value.lower())
            if name in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
                self.assertEqual(value, "")

    def test_eval_runner_writes_a_score(self):
        module = _load_eval()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "results.md"
            buffer = StringIO()
            with patch("sys.stdout", buffer):
                code = module.main(["--output", str(output)])
            self.assertEqual(code, 0)
            self.assertIn("Passed", buffer.getvalue())
            text = output.read_text(encoding="utf-8")
        match = re.search(r"Passed: (\d+) / (\d+)", text)
        self.assertIsNotNone(match)
        passed = int(match.group(1))
        total = int(match.group(2))
        self.assertGreaterEqual(total, 10)
        self.assertEqual(passed, total)
        self.assertIn("cancel-cw-1002", text)
        self.assertIn("Costly failures: 0", text)
        self.assertIn("## By intent", text)
        self.assertIn("Holdout passed:", text)

    def _refund_item(self, booking_id: str = "CW-1002", amount: int = 340) -> dict:
        return {
            "id": "money", "question": "x", "kind": "refund", "intent": "refund_confirmed",
            "booking_id": booking_id, "expect_amount": amount, "tool": "calculate_refund",
        }

    def test_the_right_amount_passes_in_any_money_format(self):
        module = _load_eval()
        for written in ("340 EUR", "€340", "€ 340", "340 €", "340 euros", "**€340**", "€340.00"):
            answer = f"You get {written} back. The booking fee is not refunded. Refunded to the same card."
            with self.subTest(written=written):
                self.assertTrue(module.score_answer(self._refund_item(), answer, ["calculate_refund"])["passed"])

    def test_a_wrong_or_invented_amount_is_costly_in_any_money_format(self):
        module = _load_eval()
        for answer in (
            "You get €345 back. The booking fee is not refunded. Same card.",
            "You get 340 EUR back, and €5 for the trouble. The booking fee is not refunded. Same card.",
            "You get €342 back. The booking fee is not refunded. Same card.",
        ):
            with self.subTest(answer=answer):
                scored = module.score_answer(self._refund_item(), answer, ["calculate_refund"])
                self.assertFalse(scored["passed"])
                self.assertGreaterEqual(scored["costly_misses"], 1)

    def test_a_blocked_booking_passes_only_without_money_back(self):
        module = _load_eval()
        item = self._refund_item("CW-1008", 0)
        good = "You cannot cancel booking CW-1008 because it is in progress, and nothing will be refunded."
        self.assertTrue(module.score_answer(item, good, ["calculate_refund"])["passed"])
        bad = module.score_answer(item, good + " You get €16 back.", ["calculate_refund"])
        self.assertGreaterEqual(bad["costly_misses"], 1)

    def test_a_rider_below_the_minimum_must_be_refused(self):
        module = _load_eval()
        item = {"id": "age", "question": "x", "kind": "fact", "intent": "age_denied"}
        self.assertTrue(module.score_answer(item, "No, the minimum for electric bikes is 16.", [])["passed"])
        allowed = module.score_answer(item, "Yes, your child is welcome to ride.", [])
        self.assertGreaterEqual(allowed["costly_misses"], 1)

    def test_a_write_intent_is_scored_by_what_the_customer_asked(self):
        module = _load_eval()
        answer = "You get 340 EUR back. The booking fee is not refunded. Refunded to the same card."
        tools = ["calculate_refund", "request_cancellation"]
        unasked = module.score_answer(self._refund_item(), answer, tools)
        self.assertEqual((unasked["passed"], unasked["costly_misses"]), (False, 0))
        either = {**self._refund_item(), "request_cancellation": "either"}
        self.assertTrue(module.score_answer(either, answer, tools)["passed"])
        self.assertTrue(module.score_answer(either, answer, ["calculate_refund"])["passed"])
        wanted = {**self._refund_item(), "request_cancellation": True}
        self.assertFalse(module.score_answer(wanted, answer, ["calculate_refund"])["passed"])

    def test_fee_added_back_is_a_costly_miss(self):
        module = _load_eval()
        item = {
            "id": "fee-trap",
            "question": "I want to cancel booking CW-1002. How much will I get back?",
            "kind": "refund",
            "intent": "refund_confirmed",
            "booking_id": "CW-1002",
            "expect_amount": 340,
            "tool": "calculate_refund",
        }
        scored = module.score_answer(item, "You get 342 EUR back on the card.", ["calculate_refund"])
        self.assertFalse(scored["passed"])
        self.assertGreaterEqual(scored["costly_misses"], 1)
        self.assertGreaterEqual(scored["weight"], module.COSTLY_WEIGHT)
        clean = module.score_answer(
            item,
            "You get 340 EUR back. The booking fee is not refunded. Refunded to the same card.",
            ["calculate_refund"],
        )
        self.assertTrue(clean["passed"])
        self.assertEqual(clean["weight"], 0)

    def test_solution_covers_required_topics(self):
        text = (ROOT / "SOLUTION.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "unittest discover",
            "calculate_refund",
            "search_policies",
            "get_booking",
            "openai",
            "gpt-4o-mini",
            "evaluation",
            "limitation",
            "shop_now",
            "grok",
            "342",
        ):
            self.assertIn(phrase, text)
        results = (ROOT / "eval" / "results.md").read_text(encoding="utf-8")
        self.assertRegex(results, r"Passed: \d+ / \d+")


if __name__ == "__main__":
    unittest.main()
