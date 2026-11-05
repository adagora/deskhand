import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*arguments: str, token: str | None = None) -> tuple[int, dict | None, str]:
    environment = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
    if token is not None:
        environment["CASEWORK_TOKEN"] = token
    done = subprocess.run(
        [sys.executable, "-m", "orchestration", *arguments],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    last = done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""
    try:
        return done.returncode, json.loads(last), done.stderr
    except ValueError:
        return done.returncode, None, done.stderr


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CliErrorTests(unittest.TestCase):
    def assert_explained(self, outcome, code: str):
        status, payload, raw = outcome
        self.assertEqual(status, 1, raw)
        self.assertIsNotNone(payload, raw)
        self.assertEqual(payload["code"], code)
        self.assertTrue(payload["message"].endswith("."))
        self.assertTrue(payload["next"].endswith("."))
        self.assertNotIn("Traceback", raw)

    def test_a_missing_token_says_what_to_set(self):
        self.assert_explained(run("status", "--case", "x"), "set_CASEWORK_TOKEN")

    def test_a_folder_that_was_never_seeded_says_to_seed_it(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assert_explained(run("queue", "--home", folder, token="t"), "home_not_seeded")
            self.assert_explained(run("status", "--case", "x", "--home", folder, token="t"), "home_not_seeded")

    def test_a_server_that_is_down_says_how_to_start_it_and_prints_no_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "identities.json").write_text("[]", encoding="utf-8")
            outcome = run("status", "--case", "x", "--home", folder, "--address", f"127.0.0.1:{closed_port()}", token="t")
            self.assert_explained(outcome, "temporal_unreachable")
            self.assertIn("temporal server start-dev", outcome[1]["next"])


if __name__ == "__main__":
    unittest.main()
