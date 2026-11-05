import contextlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from casework.model import Refusal
from cockpit.evidence import console_link_for, web_address
from cockpit.launch import (
    PROXY_ALLOW, Helper, build_of, console_command, dashboard_command, helper_environment, parser, proxy_command,
)


def child(script: str) -> list[str]:
    return [sys.executable, "-c", script]


class AddressTests(unittest.TestCase):
    def test_a_web_address_has_a_scheme_of_http_or_https_and_a_host(self):
        self.assertEqual(web_address("http://127.0.0.1:4317/"), "http://127.0.0.1:4317/")
        self.assertEqual(web_address("https://example.test/a#b"), "https://example.test/a#b")

    def test_anything_else_is_refused_before_it_can_become_a_link(self):
        for bad in ("javascript:alert(1)", "ftp://example.test", "http://", "127.0.0.1:4317", "", "data:text/html,x"):
            with self.assertRaises(ValueError, msg=bad):
                web_address(bad)

    def test_a_console_link_keeps_the_token_and_names_the_page(self):
        self.assertEqual(console_link_for("http://127.0.0.1:8793/#token=abc_DEF-1", "overview"), "http://127.0.0.1:8793/#/overview?token=abc_DEF-1")
        self.assertEqual(console_link_for("http://127.0.0.1:8793/", "overview"), "http://127.0.0.1:8793/#/overview")
        self.assertIsNone(console_link_for(None, "overview"))
        self.assertIsNone(console_link_for("", "overview"))


class CommandTests(unittest.TestCase):
    def test_the_console_reads_only_unless_a_reviewer_is_named(self):
        command = console_command(Path("/a/cli.js"), Path("/l/audit.jsonl"), 8793)
        self.assertEqual(command, ["node", "/a/cli.js", "console", "--log", "/l/audit.jsonl", "--port", "8793"])
        self.assertNotIn("--reviewer", command)

    def test_the_console_takes_the_gateway_the_metrics_and_the_reviewer_when_given(self):
        command = console_command(Path("/a/cli.js"), Path("/l/audit.jsonl"), 8793, config=Path("/g/gateway.json"), metrics_url="http://127.0.0.1:8787/metrics", reviewer="ada")
        for flag, value in (("--config", "/g/gateway.json"), ("--metrics", "http://127.0.0.1:8787/metrics"), ("--reviewer", "ada")):
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_the_dashboard_watches_one_runs_folder_on_one_port(self):
        self.assertEqual(dashboard_command(Path("/i/cli.js"), Path("/i/runs"), 4317), ["node", "/i/cli.js", "dashboard", "/i/runs", "--port", "4317"])

    def test_the_proxy_redacts_and_never_carries_the_provider_key_in_its_arguments(self):
        command = proxy_command(Path("/a/cli.js"), Path("/l/audit.jsonl"), 8787, Path("/g/gateway.json"), "https://api.openai.com")
        self.assertEqual(command[command.index("--mode") + 1], "redact")
        self.assertEqual(command[command.index("--allow-hashes") + 1], str(PROXY_ALLOW))
        self.assertTrue(PROXY_ALLOW.is_file())
        self.assertFalse([part for part in command if part.startswith("sk-")])

    def test_a_helper_gets_a_path_and_what_it_is_told_and_nothing_else(self):
        self.assertEqual(set(helper_environment()), {"PATH"})
        self.assertEqual(helper_environment(KEY="x")["KEY"], "x")


class BuildTests(unittest.TestCase):
    def test_a_folder_without_a_build_is_refused_with_the_given_code(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(Refusal) as seen:
                build_of(Path(folder), "auditline_not_built")
        self.assertEqual(seen.exception.code, "auditline_not_built")

    def test_a_folder_with_a_build_gives_its_command_line_program(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = Path(folder) / "dist" / "src" / "cli.js"
            cli.parent.mkdir(parents=True)
            cli.write_text("", encoding="utf-8")
            self.assertEqual(build_of(Path(folder), "intentgauge_not_built"), cli)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.helpers = []
        self.addCleanup(lambda: [helper.stop() for helper in self.helpers])

    def start(self, script: str) -> Helper:
        helper = Helper("test", child(script), helper_environment())
        self.helpers.append(helper)
        return helper

    def test_a_helper_is_ready_when_it_prints_its_event_after_some_noise(self):
        helper = self.start('import sys,time,json; print("plain line"); sys.stdout.flush(); time.sleep(0.3); print(json.dumps({"event":"listening","url":"http://127.0.0.1:1"}), file=sys.stderr, flush=True); time.sleep(30)')
        self.assertEqual(helper.wait_for('"listening"', timeout=10)["url"], "http://127.0.0.1:1")

    def test_an_event_printed_just_before_the_helper_exits_is_still_found(self):
        helper = self.start('import sys,json; print(json.dumps({"dashboard":"http://127.0.0.1:2"}), flush=True)')
        self.assertEqual(helper.wait_for('"dashboard"', timeout=10)["dashboard"], "http://127.0.0.1:2")

    def test_a_helper_that_exits_without_its_event_is_refused_quickly(self):
        helper = self.start('import sys; print("no event", file=sys.stderr)')
        started = time.monotonic()
        with self.assertRaises(Refusal) as seen:
            helper.wait_for('"url"', timeout=10)
        self.assertEqual(seen.exception.code, "helper_did_not_start")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_helper_that_stays_silent_is_refused_at_the_deadline(self):
        helper = self.start("import time; time.sleep(30)")
        with self.assertRaises(Refusal) as seen:
            helper.wait_for('"url"', timeout=0.6)
        self.assertEqual(seen.exception.code, "helper_did_not_start")

    def test_stopping_ends_a_helper_that_is_still_running(self):
        helper = self.start("import time; time.sleep(60)")
        self.assertIsNone(helper.process.poll())
        helper.stop()
        self.assertIsNotNone(helper.process.poll())


class ParserTests(unittest.TestCase):
    def test_the_auditline_folder_is_required_and_the_ports_have_the_usual_defaults(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser().parse_args([])
        args = parser().parse_args(["--auditline", "/a"])
        self.assertEqual((args.cockpit_port, args.console_port, args.dashboard_port, args.proxy_port), (8799, 8793, 4317, 8787))
        self.assertIsNone(args.intentgauge)
        self.assertIsNone(args.proxy_config)
        self.assertIsNone(args.console_reviewer)

    def test_a_provider_address_that_is_not_a_web_address_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser().parse_args(["--auditline", "/a", "--upstream-openai", "javascript:alert(1)"])


if __name__ == "__main__":
    unittest.main()
