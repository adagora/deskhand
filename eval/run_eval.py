"""Run the shipped agent on eval/questions.json and write a results file.

The default is the deterministic fake model, which needs no API key and no network.
--provider runs the same questions on a hosted model, and those calls are billable.
Refund checks call calculate_refund for that booking.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from citywheels.agent import run_agent
from citywheels.bookings import get_booking
from citywheels.cases import calculate_refund
from citywheels.cli import build_model
from citywheels.clock import shop_now
from citywheels.fake_model import FakeModel
from citywheels.schedule import change_allowed
from citywheels.settings import current

QUESTIONS = Path(__file__).resolve().parent / "questions.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results.md"
FAKE_LINE = "Model: fake deterministic tool-calling stand-in. No API key and no network."


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _has(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


_MONEY = re.compile(
    r"€\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:EUR\b|euros?\b|€)",
    re.IGNORECASE,
)
_REFUSES_CANCEL = re.compile(r"(?:cannot|can't|can’t|not able to)\s+(?:be\s+)?cancel", re.IGNORECASE)
_REFUSES_RIDER = re.compile(r"\b(?:cannot|can't|can’t|not allowed|no)\b", re.IGNORECASE)


def money_mentions(answer: str) -> set[Decimal]:
    amounts = set()
    for match in _MONEY.finditer(answer):
        amounts.add(Decimal((match.group(1) or match.group(2)).replace(",", ".")))
    return amounts


def _known_amounts(computed: dict) -> set[Decimal]:
    fields = ("refund_eur", "rental_refund_eur", "deposit_refund_eur", "booking_fee_eur", "rental_price_eur", "deposit_eur")
    return {Decimal(str(computed[name])) for name in fields if computed.get(name) is not None}


# A costly miss can lose money, strand a rider, or invent a booking.
# An ordinary miss is a missing detail. The pass count is still one row per question.
COSTLY_WEIGHT = 5
ORDINARY_WEIGHT = 1


def score_answer(item: dict, answer: str, tools: list[str], data_dir: Path | None = None) -> dict:
    """Score one reply. Refund figures are checked against calculate_refund, not a judge model."""
    notes: list[dict] = []

    def miss(text: str, costly: bool) -> None:
        notes.append({"text": text, "costly": costly})

    if item.get("tool") and item["tool"] not in tools:
        miss(f"expected tool {item['tool']}, called {tools or 'nothing'}", False)
    if item.get("expect_no_tools") and tools:
        miss("expected no tool calls, called " + ", ".join(tools), False)
    wanted = item.get("request_cancellation")
    if wanted is True and "request_cancellation" not in tools:
        miss("the customer asked to cancel and request_cancellation was not called", False)
    if wanted is None and "request_cancellation" in tools:
        miss("requested a cancellation the customer did not ask for", False)

    kind = item["kind"]
    intent = item.get("intent") or kind
    if kind == "refund":
        computed = calculate_refund(item["booking_id"], data_dir=data_dir)
        amount = computed["refund_eur"]
        if item.get("expect_amount") is not None and amount != item["expect_amount"]:
            miss(
                f"calculate_refund returned {amount}, question file expected {item['expect_amount']}",
                True,
            )
        mentioned = money_mentions(answer)
        if amount and Decimal(str(amount)) not in mentioned:
            miss(f"answer does not state the refund of {amount} EUR", True)
        invented = sorted(mentioned - _known_amounts(computed) - {Decimal(0)})
        if invented:
            miss("answer states an amount the tool did not return: " + ", ".join(map(str, invented)), True)
        if computed.get("can_cancel"):
            if "not refunded" not in answer.lower():
                miss("answer does not say the booking fee is not refunded", False)
            if computed.get("channel") == "card" and "card" not in answer.lower():
                miss("answer does not name the card refund path", True)
            if computed.get("channel") == "voucher" and (
                "voucher" not in answer.lower() or "cash" not in answer.lower()
            ):
                miss("answer does not say the refund is a voucher and not cash", True)
            fee = computed.get("booking_fee_eur") or 0
            if fee and str(amount + fee) in answer:
                miss(f"answer contains {amount + fee}, which adds the booking fee", True)
        else:
            if not _REFUSES_CANCEL.search(answer):
                miss("answer does not say the booking cannot be cancelled", True)
            status = str(computed.get("status"))
            if status not in answer and status.replace("_", " ") not in answer:
                miss(f"answer does not mention status {status}", False)
            fee = Decimal(str(computed.get("booking_fee_eur") or 0))
            if amount == 0 and any(value not in (Decimal(0), fee) for value in money_mentions(answer)):
                miss("answer offers money back on a booking that cannot be cancelled", True)
    elif kind == "not_found":
        computed = calculate_refund(item["booking_id"], data_dir=data_dir)
        if computed.get("found"):
            miss("calculate_refund unexpectedly found the booking", True)
        if "not found" not in answer.lower():
            miss("answer does not say the booking was not found", True)
        if money_mentions(answer):
            miss("answer invents a refund amount for a missing booking", True)
    elif kind == "refuse":
        if "staff" not in answer.lower():
            miss("answer does not tell the customer to contact the staff", False)
        if "canberra" in answer.lower():
            miss("answer states Canberra", True)
    elif intent == "weather":
        if "staff" not in answer.lower():
            miss("storm refund is not sent to the staff", True)
        if _promises_automatic_storm_refund(answer):
            miss("answer grants a storm refund automatically", True)
    elif intent == "booking_change":
        _score_change(item, answer, miss, data_dir)
    elif intent == "age_denied":
        if not _REFUSES_RIDER.search(answer):
            miss("answer does not refuse a rider below the age minimum", True)
        if "16" not in answer:
            miss("answer does not cite the age minimum of 16", False)
    elif intent == "age_allowed":
        lowered = answer.lower()
        allows = "yes" in lowered or "can rent" in lowered
        refuses = "cannot" in lowered or "can't" in lowered or "not allowed" in lowered
        if refuses and not allows:
            miss("answer refuses a rider who meets the age minimum", True)
        elif not allows:
            miss("answer does not allow a rider who meets the age minimum", True)
        if "16" not in answer:
            miss("answer does not cite the age minimum of 16", False)

    for text in item.get("expect_all") or []:
        if not _has(answer, text):
            miss(f"missing {text!r}", text in (item.get("costly_expect") or []))
    for text in item.get("forbid") or []:
        if _has(answer, text):
            miss(f"forbidden text {text!r} is in the answer", text in (item.get("costly_forbid") or []))

    costly = sum(1 for note in notes if note["costly"])
    ordinary = len(notes) - costly
    return {
        "id": item["id"],
        "question": item["question"],
        "kind": kind,
        "intent": intent,
        "holdout": bool(item.get("holdout")),
        "passed": not notes,
        "notes": [note["text"] for note in notes],
        "costly_misses": costly,
        "ordinary_misses": ordinary,
        "weight": costly * COSTLY_WEIGHT + ordinary * ORDINARY_WEIGHT,
        "answer": answer,
        "tools": tools,
    }


def _promises_automatic_storm_refund(answer: str) -> bool:
    lowered = answer.lower()
    if "not automatically" in lowered or "does not automatically" in lowered:
        return False
    return re.search(r"you (will|do) (automatically )?get a full refund", lowered) is not None


def _score_change(item: dict, answer: str, miss, data_dir: Path | None) -> None:
    looked = get_booking(item["booking_id"], data_dir=data_dir)
    if not looked.get("found"):
        miss("change question names a booking the file does not contain", True)
        return
    booking = looked["booking"]
    start = datetime.fromisoformat(str(booking["start"]))
    allowed = change_allowed(status=str(booking.get("status")), start=start, now=shop_now())
    lowered = answer.lower()
    grants = "can be changed" in lowered or "for free" in lowered or "yes" in lowered
    refuses = "not possible" in lowered or "cannot be changed" in lowered or "can't be changed" in lowered
    if allowed:
        if refuses and not grants:
            miss("answer refuses a change that is still 24 hours out", True)
        elif not grants:
            miss("answer does not say the change is still possible", False)
    elif grants and not refuses:
        miss("answer allows a change inside 24 hours of the start", True)
    elif not refuses:
        miss("answer does not say the change is not possible", False)


def check_question(item: dict, data_dir: Path | None = None, model=None) -> dict:
    try:
        result = run_agent(item["question"], model or FakeModel(), data_dir=data_dir)
    except (RuntimeError, OSError) as error:
        scored = score_answer(item, "", [], data_dir=data_dir)
        scored["notes"].insert(0, f"the model call failed: {error}")
        scored["passed"] = False
        return scored
    names = [step["name"] for step in result.tool_trace]
    scored = score_answer(item, result.answer, names, data_dir=data_dir)
    return scored


def render(rows: list[dict], model_line: str = FAKE_LINE) -> str:
    passed = sum(1 for row in rows if row["passed"])
    total = len(rows)
    costly = sum(row["costly_misses"] for row in rows)
    ordinary = sum(row["ordinary_misses"] for row in rows)
    weight = sum(row["weight"] for row in rows)
    lines = [
        "# CityWheels evaluation",
        "",
        model_line,
        "",
        f"Passed: {passed} / {total}",
        "",
        f"Costly failures: {costly}",
        f"Ordinary failures: {ordinary}",
        f"Weighted miss: {weight}",
        "",
        "A question still counts once in the pass line above. A costly miss is a wrong amount, a fee added back, a refund on a booking that cannot be cancelled, an invented booking, an automatic storm refund, a change inside 24 hours, or an age rule that lets the wrong rider through. Each costly miss weighs "
        f"{COSTLY_WEIGHT}. Each ordinary miss weighs {ORDINARY_WEIGHT}.",
        "",
        "| Id | Result | Intent | Holdout | Weight | Question | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        result = "pass" if row["passed"] else "fail"
        note = " ".join(row["notes"]) if row["notes"] else ""
        lines.append(
            "| {id} | {result} | {intent} | {holdout} | {weight} | {question} | {note} |".format(
                id=_cell(row["id"]),
                result=result,
                intent=_cell(row["intent"]),
                holdout="yes" if row["holdout"] else "",
                weight=row["weight"],
                question=_cell(row["question"]),
                note=_cell(note),
            )
        )
    lines.extend(["", "## By intent", "", "| Intent | Passed | Questions | Costly misses |", "| --- | --- | --- | --- |"])
    intents: dict[str, list] = {}
    for row in rows:
        intents.setdefault(row["intent"], []).append(row)
    for intent in sorted(intents):
        group = intents[intent]
        ok = sum(1 for row in group if row["passed"])
        lines.append(
            f"| {_cell(intent)} | {ok} | {len(group)} | {sum(row['costly_misses'] for row in group)} |"
        )
    holdout = [row for row in rows if row["holdout"]]
    lines.extend(["", "## Holdout", ""])
    if not holdout:
        lines.append("No holdout questions in this file.")
    else:
        ok = sum(1 for row in holdout if row["passed"])
        lines.append(
            f"Holdout passed: {ok} / {len(holdout)}. These questions were not used to edit the system prompt."
        )
    lines.extend(["", "## Failures", ""])
    failures = [row for row in rows if not row["passed"]]
    if not failures:
        lines.append("No question failed in this run.")
    else:
        for row in failures:
            lines.append(f"### {row['id']}")
            lines.append("")
            lines.append(row["question"])
            lines.append("")
            lines.append("Check: " + "; ".join(row["notes"]))
            lines.append("")
            lines.append("Answer:")
            lines.append("")
            lines.append(row["answer"])
            lines.append("")
    lines.append("")
    return "\n".join(lines)


def _cell(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "/")


def run(output: Path, questions_path: Path = QUESTIONS, provider: str | None = None) -> tuple[int, int]:
    model, line = None, FAKE_LINE
    if provider:
        settings = current(provider)
        model = build_model(settings)
        name = settings.openai_model if provider == "openai" else settings.gemini_model
        line = f"Model: {provider} {name}, called live. This file is not regenerated by the tests."
    rows = [check_question(item, model=model) for item in load_questions(questions_path)]
    output.write_text(render(rows, line), encoding="utf-8")
    passed = sum(1 for row in rows if row["passed"])
    return passed, len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the CityWheels help desk agent.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--provider", choices=("openai", "gemini"), help="Run on a hosted model. Billable.")
    args = parser.parse_args(argv)
    if args.provider and args.output == DEFAULT_OUTPUT:
        parser.error("--provider needs its own --output, so eval/results.md stays the fake-model score")
    passed, total = run(args.output, args.questions, args.provider)
    print(f"Passed {passed} / {total}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
