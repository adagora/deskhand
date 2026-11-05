import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from casework.messages import EXPLANATIONS, NEEDS_A_PERSON, PHASES, explain, phase_label

SOURCES = [path for folder in ("casework", "orchestration", "citywheels", "cockpit") for path in (ROOT / folder).glob("*.py")]
TEXT = "\n".join(path.read_text(encoding="utf-8") for path in SOURCES if path.name != "messages.py")
STAFF_CODES = {"backend_evidence_unavailable", "cancelled_refund_unsettled", "readback_mismatch", "evidence_unavailable"}


class ExplanationTests(unittest.TestCase):
    def test_every_refusal_code_in_the_source_has_a_sentence_and_a_next_step(self):
        codes = set(re.findall(r'Refusal\("([a-z_A-Z]+)"\)', TEXT))
        self.assertGreater(len(codes), 20)
        self.assertEqual(sorted(codes - set(EXPLANATIONS)), [])

    def test_every_staff_code_is_explained(self):
        self.assertEqual(sorted(STAFF_CODES - set(EXPLANATIONS)), [])

    def test_every_phase_the_workflow_sets_has_a_label(self):
        phases = set(re.findall(r'self\._phase = "([a-z-]+)"', TEXT))
        self.assertIn("waiting-for-review", phases)
        self.assertEqual(sorted(phases - set(PHASES)), [])

    def test_the_phases_that_need_a_person_exist(self):
        self.assertEqual(sorted(set(NEEDS_A_PERSON) - set(PHASES)), [])

    def test_an_explanation_is_a_plain_sentence_with_a_next_step(self):
        for code, (message, next_step) in EXPLANATIONS.items():
            self.assertTrue(message.endswith("."), code)
            self.assertTrue(next_step.endswith("."), code)
            self.assertNotIn("Traceback", message + next_step)

    def test_an_unknown_code_still_gets_an_answer(self):
        answer = explain("something_new")
        self.assertEqual(answer["code"], "something_new")
        self.assertIn("maintainer", answer["next"])
        self.assertEqual(phase_label("unmapped"), "unmapped")


if __name__ == "__main__":
    unittest.main()
