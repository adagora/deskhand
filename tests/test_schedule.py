import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.clock import FIXED_NOW
from citywheels.schedule import (
    age_verdict,
    applicable_hours,
    change_allowed,
    storm_needs_staff,
)

POLICY = """
- April to October: every day, 08:00–20:00.
- November to March: Monday to Saturday, 09:00–17:00.
The staff decide each case.
"""


class ScheduleTests(unittest.TestCase):
    def test_change_window_is_inclusive_at_24_hours(self):
        start = FIXED_NOW + timedelta(hours=24)
        self.assertTrue(change_allowed(status="confirmed", start=start, now=FIXED_NOW))
        self.assertFalse(
            change_allowed(
                status="confirmed",
                start=start - timedelta(seconds=1),
                now=FIXED_NOW,
            )
        )
        self.assertFalse(change_allowed(status="in_progress", start=start, now=FIXED_NOW))

    def test_hours_come_from_the_policy_text(self):
        chosen = applicable_hours(POLICY, FIXED_NOW)
        self.assertIsNotNone(chosen)
        self.assertIn("08:00", chosen.hours)
        self.assertNotIn("09:00", chosen.hours)

    def test_age_and_storm_do_not_need_files(self):
        self.assertEqual(age_verdict(14, 16, guardian_below=18), "denied")
        self.assertEqual(age_verdict(17, 16, guardian_below=18), "guardian")
        self.assertTrue(storm_needs_staff(POLICY))
        self.assertFalse(storm_needs_staff("Bikes are fun."))

    def test_desk_checker_accepts_the_real_functions(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "verify" / "check_desk.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok 12", proc.stdout)


if __name__ == "__main__":
    unittest.main()
