"""Pins for the agent-facing CityWheels command surface."""

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.cli import main
from citywheels.settings import Settings
from citywheels.surface import ASK, near_options, prepare_argv, unique_near

CW1002 = "I want to cancel booking CW-1002. How much will I get back?"


def _run(argv):
    stdout = StringIO()
    stderr = StringIO()
    with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class SurfaceContractTests(unittest.TestCase):
    def test_bare_invocation_prints_triage(self):
        code, stdout, stderr = _run([])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn(ASK, stdout)
        self.assertIn("--robot-triage", stdout)
        self.assertIn("capabilities", stdout)
        self.assertIn("robot-docs guide", stdout)
        self.assertIn("--help", stdout)
        self.assertNotIn("\x1b", stdout)

    def test_help_names_agent_commands(self):
        code, stdout, stderr = _run(["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("capabilities", stdout)
        self.assertIn("robot-docs guide", stdout)
        self.assertIn("--robot-triage", stdout)
        self.assertIn("0  success", stdout)
        short_code, short_out, _short_err = _run(["-h"])
        self.assertEqual(short_code, 0)
        self.assertIn("--robot-triage", short_out)
        help_code, help_out, _help_err = _run(["help"])
        self.assertEqual(help_code, 0)
        self.assertEqual(help_out, stdout)
        robot_code, robot_out, robot_err = _run(["--robot-help"])
        self.assertEqual(robot_code, 0)
        self.assertEqual(robot_err, "")
        self.assertIn("Exit codes:", robot_out)

    def test_version_is_the_contract(self):
        code, stdout, _stderr = _run(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "1\n")

    def test_human_answer_stays_plain_text(self):
        code, stdout, stderr = _run(["--provider", "fake", CW1002])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("340 EUR", stdout)
        self.assertNotIn("342", stdout)
        self.assertFalse(stdout.startswith("{"))

    def test_json_answer_is_one_object(self):
        code, stdout, stderr = _run(["--provider", "fake", "--json", CW1002])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout.count("\n"), 1)
        payload = json.loads(stdout)
        self.assertEqual(
            list(payload.keys()),
            ["answer", "contract_version", "ok", "provider", "tool_trace"],
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contract_version"], "1")
        self.assertEqual(payload["provider"], "fake")
        self.assertIn("340 EUR", payload["answer"])
        self.assertNotIn("342", payload["answer"])
        self.assertIsInstance(payload["tool_trace"], list)
        again, _out2, _err2 = _run(["--json", "--provider", "fake", CW1002])
        self.assertEqual(again, 0)
        self.assertEqual(_out2, stdout)

    def test_typo_flags_and_provider_still_answer(self):
        code, stdout, stderr = _run(["--jsno", "--providor", "fak", CW1002])
        self.assertEqual(code, 0, stderr)
        self.assertIn("corrected '--jsno' to '--json'", stderr)
        self.assertIn("corrected '--providor' to '--provider'", stderr)
        self.assertIn("corrected provider value 'fak' to 'fake'", stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("340 EUR", payload["answer"])
        self.assertNotIn("\x1b", stdout + stderr)

    def test_unknown_flag_names_help(self):
        code, stdout, stderr = _run(["--nope"])
        self.assertEqual(code, 1)
        self.assertNotIn("corrected", stderr)
        self.assertIn("Where: --nope.", stderr)
        self.assertIn("Next: python3 -m citywheels --help", stderr)
        self.assertEqual(stdout, "")

    def test_unknown_provider_flag_exits_1(self):
        code, stdout, stderr = _run(["--provider", "nope", "--json", "hello"])
        self.assertEqual(code, 1)
        self.assertIn("fake, openai, gemini", stderr)
        self.assertIn("Where: --provider.", stderr)
        self.assertIn(ASK, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["category"], "user-input-error")
        self.assertEqual(payload["error"]["hint"], ASK)

    def test_flags_without_a_question_exit_1(self):
        code, stdout, stderr = _run(["--json"])
        self.assertEqual(code, 1)
        self.assertIn("Where: question.", stderr)
        self.assertIn(ASK, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["error"]["category"], "user-input-error")

    def test_capabilities_and_robot_docs(self):
        code, stdout, stderr = _run(["capabilities"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual([item["code"] for item in payload["exit_codes"]], [0, 1, 2, 3, 4])
        wrapped_code, wrapped, _err = _run(["capabilities", "--json"])
        self.assertEqual(wrapped_code, 0)
        self.assertEqual(wrapped, stdout)
        guide_code, guide, guide_err = _run(["robot-docs", "guide"])
        self.assertEqual(guide_code, 0)
        self.assertEqual(guide_err, "")
        self.assertIn(ASK, guide)
        self.assertNotIn("\x1b", guide)
        json_code, guide_json, _json_err = _run(["robot-docs", "--json", "guide"])
        self.assertEqual(json_code, 0)
        document = json.loads(guide_json)
        self.assertTrue(document["ok"])
        self.assertEqual(document["guide"], guide)

    def test_robot_triage_is_deterministic_and_skips_the_model(self):
        with patch("citywheels.cli.run_agent", side_effect=AssertionError("model called")):
            code, stdout, stderr = _run(["--robot-triage"])
            verb_code, verb_out, verb_err = _run(["robot-triage"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(verb_code, 0, verb_err)
        self.assertEqual(verb_out, stdout)
        again, again_out, _again_err = _run(["--robot-triage", "--json", "--no-color"])
        self.assertEqual(again, 0)
        self.assertEqual(again_out, stdout)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contract_version"], "1")
        self.assertEqual(payload["health"]["policies"], ["cancellations.md", "faq.md", "prices.md"])
        self.assertEqual(payload["health"]["booking_count"], 10)
        self.assertTrue(payload["health"]["bookings_readable"])
        self.assertEqual(payload["recommendations"], sorted(payload["recommendations"], key=payload["recommendations"].index))
        self.assertIn(ASK, payload["quick_ref"])
        self.assertTrue(payload["recommendations"][0].startswith("For a deterministic offline answer"))

    def test_robot_triage_rejects_a_question(self):
        with patch("citywheels.cli.run_agent", side_effect=AssertionError("model called")):
            code, stdout, stderr = _run(["--robot-triage", CW1002])
        self.assertEqual(code, 1)
        self.assertIn("Where: --robot-triage.", stderr)
        self.assertIn("Next: python3 -m citywheels --robot-triage", stderr)
        self.assertEqual(stdout, "")

    def test_double_dash_keeps_capabilities_as_a_question(self):
        code, stdout, stderr = _run(["--provider", "fake", "--", "capabilities"])
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("exit_codes", stdout)
        self.assertIn("help@citywheels.example", stdout)

    def test_missing_data_dir_names_the_path(self):
        missing = ROOT / "no-such-citywheels-data"
        code, _stdout, stderr = _run(["--data-dir", str(missing), "--provider", "fake", "hello"])
        self.assertEqual(code, 1)
        self.assertIn(str(missing), stderr)
        self.assertIn("Where: --data-dir.", stderr)
        self.assertIn(ASK, stderr)

    def test_unreadable_bookings_stay_in_the_health_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "policies").mkdir()
            (path / "bookings.json").write_text("{", encoding="utf-8")
            code, stdout, stderr = _run(["--robot-triage", "--data-dir", str(path)])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["health"]["bookings_readable"])
        self.assertEqual(payload["health"]["booking_count"], 0)
        self.assertEqual(payload["health"]["policies"], [])

    def test_missing_api_key_exits_3(self):
        settings = Settings(
            provider="openai",
            openai_key="",
            openai_model="gpt-4o-mini",
            openai_base_url="https://api.openai.com/v1",
            gemini_key="",
            gemini_model="gemini-3.8-flash",
        )
        with patch("citywheels.cli.current", return_value=settings):
            code, stdout, stderr = _run(["--provider", "openai", "--json", "hello"])
        self.assertEqual(code, 3)
        self.assertIn("Where: OPENAI_API_KEY.", stderr)
        self.assertIn(ASK, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["error"]["category"], "tool-environment-error")
        self.assertEqual(payload["error"]["hint"], ASK)

    def test_unknown_env_provider_exits_3(self):
        with patch.dict(os.environ, {"CITYWHEELS_PROVIDER": "nope"}, clear=False):
            code, stdout, stderr = _run(["--json", "hello"])
        self.assertEqual(code, 3, stderr)
        self.assertIn("Where: CITYWHEELS_PROVIDER.", stderr)
        self.assertIn("fake, openai, gemini", stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["error"]["category"], "tool-environment-error")

    def test_env_provider_typo_uses_fake(self):
        with patch.dict(os.environ, {"CITYWHEELS_PROVIDER": "fak"}, clear=False):
            code, stdout, stderr = _run([CW1002])
        self.assertEqual(code, 0, stderr)
        self.assertIn("corrected CITYWHEELS_PROVIDER 'fak' to 'fake'", stderr)
        self.assertIn("340 EUR", stdout)
        self.assertNotIn("342", stdout)

    def test_urlerror_exits_4(self):
        with patch("citywheels.cli.run_agent", side_effect=urllib.error.URLError("timed out")):
            code, stdout, stderr = _run(["--provider", "fake", "--json", "hello"])
        self.assertEqual(code, 4)
        self.assertIn("Where: network.", stderr)
        self.assertIn(ASK, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["error"]["category"], "transient-failure")
        self.assertEqual(payload["error"]["hint"], ASK)

    def test_listed_typos_correct_before_parse(self):
        flags = {
            "--jsno": "--json",
            "--jason": "--json",
            "--providor": "--provider",
            "--provder": "--provider",
            "--data-dr": "--data-dir",
        }
        for source, target in flags.items():
            corrected, _notes, ambiguous, where = prepare_argv([source])
            self.assertEqual(corrected, [target], source)
            self.assertIsNone(ambiguous)
            self.assertIsNone(where)
        values = {"fak": "fake", "gemeni": "gemini", "open-ai": "openai"}
        for source, target in values.items():
            corrected, notes, ambiguous, _where = prepare_argv(["--provider", source])
            self.assertEqual(corrected, ["--provider", target], notes)
            self.assertIsNone(ambiguous)
        corrected, _notes, ambiguous, _where = prepare_argv(["--provider=fak"])
        self.assertEqual(corrected, ["--provider=fake"])
        self.assertIsNone(ambiguous)

    def test_two_candidates_are_not_guessed(self):
        options = ("--ac", "--ad")
        self.assertEqual(near_options("--ab", options), options)
        self.assertIsNone(unique_near("--ab", options))
        with patch("citywheels.surface.KNOWN_FLAGS", options):
            corrected, _notes, ambiguous, where = prepare_argv(["--ab", "question"])
            code, stdout, stderr = _run(["--ab"])
        self.assertEqual(corrected, ["--ab", "question"])
        self.assertIn("equally close", ambiguous)
        self.assertEqual(where, "--ab")
        self.assertEqual(code, 1)
        self.assertIn("equally close to --ac, --ad", stderr)
        self.assertIn("Next: python3 -m citywheels --help", stderr)
        self.assertNotIn("corrected", stderr)
        self.assertEqual(stdout, "")

    def test_no_color_adds_no_escapes(self):
        code, stdout, stderr = _run(["--no-color", "--provider", "fake", CW1002])
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", stdout + stderr)
        self.assertIn("340 EUR", stdout)


if __name__ == "__main__":
    unittest.main()
