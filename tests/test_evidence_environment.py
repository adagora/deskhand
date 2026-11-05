import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from casework.evidence import AuditCli, NoEvidence, evidence_from_environment


class EvidenceEnvironmentTests(unittest.TestCase):
    def test_nothing_is_recorded_unless_both_a_command_and_a_log_are_named(self):
        self.assertIsInstance(evidence_from_environment({}), NoEvidence)
        self.assertIsInstance(evidence_from_environment({"AUDITLINE_CLI": "cli.js"}), NoEvidence)
        self.assertIsInstance(evidence_from_environment({"AI_AUDIT_LOG": "a.jsonl"}), NoEvidence)

    def test_the_older_names_still_work(self):
        evidence = evidence_from_environment({"AI_AUDIT_CLI": "old.js", "AI_AUDIT_LOG": "old.jsonl"})
        self.assertIsInstance(evidence, AuditCli)
        self.assertEqual((evidence.command, evidence.log), (("node", "old.js"), Path("old.jsonl")))

    def test_the_new_names_work_and_win_when_both_are_set(self):
        evidence = evidence_from_environment({"AUDITLINE_CLI": "new.js", "AUDITLINE_LOG": "new.jsonl"})
        self.assertEqual((evidence.command, evidence.log), (("node", "new.js"), Path("new.jsonl")))
        mixed = evidence_from_environment({"AUDITLINE_CLI": "new.js", "AI_AUDIT_CLI": "old.js", "AI_AUDIT_LOG": "old.jsonl"})
        self.assertEqual((mixed.command, mixed.log), (("node", "new.js"), Path("old.jsonl")))


if __name__ == "__main__":
    unittest.main()
