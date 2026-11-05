import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.agent import run_agent
from citywheels.cli import main
from citywheels.clock import FIXED_NOW
from citywheels.fake_model import FakeModel
from citywheels.messages import STAFF_EMAIL, STAFF_PHONE
from citywheels.model import ModelTurn, ToolCall
from citywheels.briefing import load_system_prompt
from citywheels.cases import calculate_refund
from citywheels.tools import TOOL_SPECS, run_tool

SUMMER_HOURS = "08:00\u201320:00"
WINTER_HOURS = "09:00\u201317:00"
CW1002 = "I want to cancel booking CW-1002. How much will I get back?"
AUSTRALIA = "What is the capital of Australia?"


class ScriptedRefundModel:
    """Asks for calculate_refund, then copies the tool's amount into the answer."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        del tools
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=[
                    ToolCall("t1", "calculate_refund", {"booking_id": "CW-1002"})
                ]
            )
        tool = next(message for message in reversed(messages) if message["role"] == "tool")
        payload = json.loads(tool["content"])
        amount = payload["refund_eur"]
        return ModelTurn(
            content=(
                f"If you cancel booking CW-1002 now, you get {amount} EUR back. "
                f"{payload['booking_fee_note']} {payload['channel_detail']}"
            )
        )


class AlwaysToolModel:
    def complete(self, messages, tools):
        del messages, tools
        return ModelTurn(
            tool_calls=[ToolCall("loop", "search_policies", {"query": "opening hours"})]
        )


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = patch("urllib.request.urlopen", side_effect=AssertionError("network"))
        self._connect = patch(
            "socket.create_connection", side_effect=AssertionError("network")
        )
        self._urlopen.start()
        self._connect.start()
        self.addCleanup(self._urlopen.stop)
        self.addCleanup(self._connect.stop)

    def test_scripted_loop_runs_calculate_refund_and_quotes_340(self):
        result = run_agent(CW1002, ScriptedRefundModel())
        self.assertEqual([step["name"] for step in result.tool_trace], ["calculate_refund"])
        self.assertEqual(result.tool_trace[0]["arguments"]["booking_id"], "CW-1002")
        self.assertEqual(result.tool_trace[0]["result"]["refund_eur"], 340)
        self.assertEqual(
            result.tool_trace[0]["result"]["refund_eur"],
            calculate_refund("CW-1002")["refund_eur"],
        )
        self.assertIn("340 EUR", result.answer)
        self.assertNotIn("342", result.answer)
        self.assertIn("not refunded", result.answer)
        self.assertIn("same card", result.answer)

    def test_fake_model_cancel_cw1002(self):
        result = run_agent(CW1002, FakeModel())
        self.assertIn("calculate_refund", [step["name"] for step in result.tool_trace])
        self.assertEqual(result.tool_trace[0]["result"]["refund_eur"], 340)
        self.assertIn("you get 340 EUR back", result.answer)
        self.assertNotIn("342", result.answer)
        self.assertIn("The booking fee is not refunded.", result.answer)
        self.assertIn("same card", result.answer)

    def test_fake_model_amount_comes_from_the_tool(self):
        start = FIXED_NOW + timedelta(days=10)
        booking = {
            "id": "CW-4242",
            "status": "confirmed",
            "bike_type": "city",
            "bikes": 1,
            "start": start.isoformat(),
            "end": (start + timedelta(hours=2)).isoformat(),
            "add_ons": [],
            "rental_price_eur": 10,
            "booking_fee_eur": 2,
            "deposit_eur": 7,
            "paid_with": "card",
        }
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "bookings.json").write_text(json.dumps([booking]), encoding="utf-8")
            result = run_agent(
                "I want to cancel booking CW-4242. How much will I get back?",
                FakeModel(),
                data_dir=directory,
            )
        self.assertEqual(result.tool_trace[0]["name"], "calculate_refund")
        self.assertEqual(result.tool_trace[0]["result"]["refund_eur"], 17)
        self.assertIn("17 EUR", result.answer)
        self.assertNotIn("19 EUR", result.answer)
        self.assertNotIn("340", result.answer)

    def test_explicit_now_reaches_calculate_refund(self):
        question = "I want to cancel booking CW-1007. How much will I get back?"
        at_fixed = run_agent(question, FakeModel(), now=FIXED_NOW)
        later = run_agent(question, FakeModel(), now=FIXED_NOW + timedelta(seconds=1))
        self.assertIn("186 EUR", at_fixed.answer)
        self.assertIn("168 EUR", later.answer)

    def test_a_question_about_what_cancelling_would_do_requests_nothing(self):
        result = run_agent("I paid CW-1004 with a voucher. What happens if I cancel it?", FakeModel())
        self.assertEqual([step["name"] for step in result.tool_trace], ["calculate_refund"])
        self.assertEqual(result.write_intents, [])
        self.assertIn("voucher", result.answer)

    def test_a_clear_request_still_names_the_cancellation(self):
        result = run_agent("Please cancel CW-1001 right now for me.", FakeModel())
        self.assertEqual(result.write_intents, [{"action": "cancel-refund", "booking_id": "CW-1001"}])

    def test_a_what_if_time_prices_that_time_and_says_which(self):
        result = run_agent(
            "What would I get back for CW-1002 if I cancel tomorrow at 9:00?", FakeModel()
        )
        step = result.tool_trace[0]
        self.assertEqual(step["arguments"]["at"], "2026-06-11T09:00:00+02:00")
        self.assertEqual(step["result"]["refund_eur"], 300)
        self.assertEqual(step["result"]["as_of"], "2026-06-11T09:00:00+02:00")
        self.assertIn("300 EUR", result.answer)
        self.assertIn("2026-06-11 09:00", result.answer)

    def test_without_a_time_the_quote_stays_the_one_for_now(self):
        quote = calculate_refund("CW-1002")
        self.assertEqual(quote["refund_eur"], 340)
        self.assertNotIn("as_of", quote)

    def test_a_what_if_time_cannot_be_earlier_than_now(self):
        earlier = datetime.fromisoformat("2026-06-09T09:00:00+02:00")
        quote = calculate_refund("CW-1002", at=earlier)
        self.assertIsNone(quote["refund_eur"])
        self.assertIn("earlier than now", quote["error"])

    def test_a_what_if_time_without_an_offset_is_shop_time(self):
        quote = calculate_refund("CW-1002", at=datetime.fromisoformat("2026-06-11T09:00:00"))
        self.assertEqual(quote["refund_eur"], 300)

    def test_a_bad_time_comes_back_as_a_tool_error_the_model_can_read(self):
        result = run_tool("calculate_refund", {"booking_id": "CW-1002", "at": "tomorrow"})
        self.assertIn("ISO 8601", result["error"])

    def test_unknown_booking_does_not_raise(self):
        result = run_agent(
            "I want to cancel booking CW-9999. How much will I get back?",
            FakeModel(),
        )
        self.assertIn("was not found", result.answer)
        self.assertNotIn("Traceback", result.answer)
        self.assertFalse(result.tool_trace[0]["result"]["found"])

    def test_australia_is_refused_without_canberra(self):
        result = run_agent(AUSTRALIA, FakeModel())
        self.assertEqual(result.tool_trace, [])
        self.assertNotIn("canberra", result.answer.lower())
        self.assertIn("staff", result.answer.lower())
        self.assertIn(STAFF_EMAIL, result.answer)
        self.assertIn(STAFF_PHONE, result.answer)

    def test_saturday_hours_use_the_summer_line(self):
        faq = (ROOT / "data" / "policies" / "faq.md").read_text(encoding="utf-8")
        self.assertIn(SUMMER_HOURS, faq)
        self.assertIn(WINTER_HOURS, faq)
        result = run_agent("What time do you open on Saturday?", FakeModel())
        self.assertEqual(result.tool_trace[0]["name"], "search_policies")
        self.assertIn(SUMMER_HOURS, result.answer)
        self.assertNotIn(WINTER_HOURS, result.answer)
        joined = "\n".join(step["result"]["passages"][0]["text"] for step in result.tool_trace)
        self.assertIn(SUMMER_HOURS, joined)

    def test_ebike_day_price_and_age(self):
        price = run_agent("How much is an e-bike for a whole day?", FakeModel())
        self.assertIn("40 EUR", price.answer)
        self.assertIn("day", price.answer.lower())
        age = run_agent("Can my 14-year-old rent an e-bike?", FakeModel())
        self.assertIn("No.", age.answer)
        self.assertIn("Electric bikes: from 16 years old.", age.answer)

    def test_booking_status_uses_get_booking(self):
        result = run_agent("What is the status of booking CW-1006?", FakeModel())
        self.assertEqual(result.tool_trace[0]["name"], "get_booking")
        self.assertEqual(result.tool_trace[0]["result"]["booking"]["status"], "completed")
        self.assertIn("completed", result.answer)
        self.assertIn("CW-1006", result.answer)

    def test_tool_loop_stops_at_max_rounds(self):
        result = run_agent("What time do you open?", AlwaysToolModel(), max_rounds=2)
        self.assertEqual(len(result.tool_trace), 1)
        self.assertIn("staff", result.answer.lower())
        self.assertIn(STAFF_EMAIL, result.answer)

    def test_last_round_offers_no_tools_and_reads_every_result(self):
        seen = []

        class Recorder:
            def complete(self, messages, tools):
                seen.append((len(tools), sum(m["role"] == "tool" for m in messages)))
                if tools:
                    return ModelTurn(
                        content="",
                        tool_calls=[ToolCall(id=str(len(seen)), name="search_policies", arguments={"query": "hours"})],
                    )
                return ModelTurn(content="We open at 08:00.")

        result = run_agent("When do you open?", Recorder(), max_rounds=2, system="s")
        self.assertEqual(seen, [(len(TOOL_SPECS), 0), (0, 1)])
        self.assertEqual(len(result.tool_trace), 1)
        self.assertEqual(result.answer, "We open at 08:00.")

    def test_tool_exception_becomes_a_tool_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bookings.json"
            path.write_text(
                json.dumps([{"id": "CW-9001", "status": "confirmed", "start": "not a date",
                             "rental_price_eur": 10, "booking_fee_eur": 2, "deposit_eur": 50,
                             "paid_with": "card"}]),
                encoding="utf-8",
            )
            result = run_tool("calculate_refund", {"booking_id": "CW-9001"}, data_dir=Path(tmp))
            self.assertIn("ValueError", result["error"])

            class OneTool:
                calls = 0

                def complete(self, messages, tools):
                    self.calls += 1
                    if self.calls == 1:
                        return ModelTurn(
                            content="",
                            tool_calls=[ToolCall(id="1", name="calculate_refund", arguments={"booking_id": "CW-9001"})],
                        )
                    self.last_tool = json.loads(messages[-1]["content"])
                    return ModelTurn(content="")

            model = OneTool()
            answer = run_agent("Cancel CW-9001", model, data_dir=Path(tmp), system="s")
            self.assertIn("error", model.last_tool)
            self.assertIn(STAFF_EMAIL, answer.answer)

    def test_tool_specs_and_prompt(self):
        self.assertEqual(
            {tool["name"] for tool in TOOL_SPECS},
            {"search_policies", "get_booking", "calculate_refund", "request_cancellation"},
        )
        prompt = load_system_prompt()
        self.assertIn("2026-06-10 09:00", prompt)
        self.assertIn(STAFF_EMAIL, prompt)
        self.assertNotIn("Canberra", prompt)
        self.assertIn("08:00", prompt)
        rules = (ROOT / "data" / "policies" / "cancellations.md").read_text(encoding="utf-8")
        self.assertIn(STAFF_EMAIL, rules)
        self.assertIn(STAFF_PHONE, rules)

    def test_unknown_tool_and_bad_json_do_not_raise(self):
        self.assertIn("error", run_tool("weather", {}))
        self.assertIn("error", run_tool("get_booking", "{"))

    def test_cli_fake_provider_cw1002(self):
        from io import StringIO

        buffer = StringIO()
        with patch("sys.stdout", buffer):
            code = main(["--provider", "fake", CW1002])
        self.assertEqual(code, 0)
        text = buffer.getvalue()
        self.assertIn("340 EUR", text)
        self.assertNotIn("342", text)
        self.assertIn("not refunded", text)
        self.assertIn("card", text.lower())

    def test_module_entry_point(self):
        env = os.environ.copy()
        for key in (
            "CITYWHEELS_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "CITYWHEELS_PROVIDER",
        ):
            env.pop(key, None)
        proc = subprocess.run(
            [sys.executable, "-m", "citywheels", "--provider", "fake", CW1002],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("340 EUR", proc.stdout)
        self.assertNotIn("342", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("not refunded", proc.stdout)
        self.assertIn("card", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
