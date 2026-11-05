"""Deterministic tool-calling stand-in.

Chooses a tool from the question, then writes the answer from the tool JSON.
It does not compute refunds and it does not answer from general knowledge.
"""

import json
import re

from datetime import datetime, timedelta

from citywheels.clock import shop_now
from citywheels.messages import STAFF_EMAIL, STAFF_PHONE, booking_not_found_message, contact_staff_message
from citywheels.model import ModelTurn, ToolCall
from citywheels.schedule import (
    age_verdict,
    applicable_hours,
    change_allowed,
    range_verdict,
    storm_needs_staff,
)

_BOOKING_RE = re.compile(r"CW-\d+", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()))


_HINT_WORDS = {
    "bike",
    "bikes",
    "rental",
    "rent",
    "booking",
    "bookings",
    "cancel",
    "cancellation",
    "refund",
    "refunds",
    "deposit",
    "helmet",
    "helmets",
    "open",
    "opening",
    "hours",
    "price",
    "prices",
    "e-bike",
    "ebike",
    "electric",
    "voucher",
    "vouchers",
    "age",
    "dog",
    "dogs",
    "train",
    "saturday",
    "sunday",
    "shop",
    "citywheels",
    "fee",
    "fees",
    "stolen",
    "payment",
    "pay",
    "lock",
    "damage",
    "weather",
    "storm",
    "kids",
    "kid",
    "abroad",
    "closed",
    "pickup",
    "late",
    "child",
    "seat",
}
_HINT_PHRASES = ("where are you", "where is the shop", "child seat", "opening hours")
_REFUND_WORDS = ("cancel", "refund", "money back", "get back", "how much will")
_CANCEL_NOW = re.compile(r"\b(please cancel|cancel it|cancel my booking|go ahead and cancel)\b", re.IGNORECASE)
_HYPOTHETICAL = re.compile(r"\b(what happens if|what if|if i|would i|should i)\b", re.IGNORECASE)
_TOMORROW_AT = re.compile(r"\btomorrow at (\d{1,2})(?::(\d{2}))?\b", re.IGNORECASE)


def _user_question(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def on_topic(question: str) -> bool:
    if _BOOKING_RE.search(question):
        return True
    lowered = question.lower()
    if any(phrase in lowered for phrase in _HINT_PHRASES):
        return True
    return bool(_words(question) & _HINT_WORDS)


def _is_change(question: str) -> bool:
    lowered = question.lower()
    return any(word in lowered for word in ("change", "move", "reschedule"))


def _is_weather(question: str) -> bool:
    lowered = question.lower()
    return "storm" in lowered or "weather" in lowered


def _what_if_time(question: str) -> str | None:
    match = _TOMORROW_AT.search(question)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    moment = shop_now() + timedelta(days=1)
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def _decide(question: str) -> list[tuple[str, dict]] | None:
    if not on_topic(question):
        return None
    match = _BOOKING_RE.search(question)
    booking_id = match.group(0).upper() if match else ""
    if _is_weather(question):
        return [("search_policies", {"query": question})]
    if _is_change(question) and booking_id:
        return [
            ("get_booking", {"booking_id": booking_id}),
            ("search_policies", {"query": "changing a booking dates or bike type"}),
        ]
    lowered = question.lower()
    if booking_id and _CANCEL_NOW.search(question) and not _HYPOTHETICAL.search(question):
        return [
            ("calculate_refund", {"booking_id": booking_id}),
            ("request_cancellation", {"booking_id": booking_id}),
        ]
    if booking_id and any(word in lowered for word in _REFUND_WORDS):
        at = _what_if_time(question)
        arguments = {"booking_id": booking_id, **({"at": at} if at else {})}
        return [("calculate_refund", arguments)]
    if booking_id:
        return [("get_booking", {"booking_id": booking_id})]
    return [("search_policies", {"query": question})]


def _clean_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    return stripped


def _body(passage: dict) -> str:
    lines = []
    for raw in passage["text"].splitlines():
        cleaned = _clean_line(raw)
        if not cleaned or cleaned.startswith("#"):
            continue
        lines.append(cleaned)
    return "\n".join(lines).strip()


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    parts = [part.strip() for part in stripped.strip("|").split("|")]
    if not parts:
        return []
    if all(re.fullmatch(r":?-{3,}:?", part) or part == "" for part in parts):
        return []
    return parts


def _is_hours(question: str) -> bool:
    lowered = question.lower()
    return any(word in lowered for word in ("open", "opening", "hours", "closed"))


def hours_answer(question: str, passages: list[dict]) -> str | None:
    if not _is_hours(question):
        return None
    blob = "\n".join(passage["text"] for passage in passages)
    chosen = applicable_hours(blob, shop_now())
    if chosen is None:
        return contact_staff_message()
    lowered = question.lower()
    extra = ""
    if "every day" in chosen.line.lower() and "saturday" in lowered:
        extra = " That includes Saturday."
    elif "every day" in chosen.line.lower() and "sunday" in lowered:
        extra = " That includes Sunday."
    return (
        f"Today is {shop_now().strftime('%Y-%m-%d')} Europe/Warsaw. {chosen.line}{extra} "
        f"Opening hours are {chosen.hours}."
    )


def _day_column(passage_text: str) -> int | None:
    for raw in passage_text.splitlines():
        cells = _cells(raw)
        lowered = [cell.lower() for cell in cells]
        if "per day" in lowered:
            return lowered.index("per day")
    return None


def day_price_answer(question: str, passages: list[dict]) -> str | None:
    lowered = question.lower()
    if not any(word in lowered for word in ("e-bike", "ebike", "electric")):
        return None
    if "day" not in lowered and "whole" not in lowered:
        return None
    for passage in passages:
        column = _day_column(passage["text"])
        if column is None:
            continue
        for raw in passage["text"].splitlines():
            cells = _cells(raw)
            if len(cells) <= column:
                continue
            label = cells[0].lower()
            if "e-bike" in label or "electric" in label:
                return f"An e-bike for a whole day is {cells[column]}."
    return contact_staff_message()


def _asked_age(question: str) -> int | None:
    match = re.search(r"(\d+)\s*-?\s*years?\s*-?\s*old", question.lower())
    if not match:
        return None
    return int(match.group(1))


def age_answer(question: str, passages: list[dict]) -> str | None:
    lowered = question.lower()
    asked = _asked_age(lowered)
    if asked is None:
        return None
    if any(word in lowered for word in ("e-bike", "ebike", "electric")):
        pattern = r"Electric bikes: from (\d+) years old\.[^\n]*"
        kind = "minimum"
    elif "city" in lowered:
        pattern = r"City bikes: from (\d+) years old\.[^\n]*"
        kind = "minimum"
    elif "kid" in lowered:
        pattern = r"Kids bikes: for children from (\d+) to (\d+) years old[^\n]*"
        kind = "range"
    else:
        return None
    blob = "\n".join(passage["text"] for passage in passages)
    match = re.search(pattern, blob)
    if not match:
        return contact_staff_message()
    sentence = match.group(0).strip()
    if kind == "range":
        low, high = int(match.group(1)), int(match.group(2))
        if range_verdict(asked, low, high) == "allowed":
            return f"Yes. {sentence}"
        return f"No. {sentence}"
    minimum = int(match.group(1))
    guardian_below = 18 if "under 18" in sentence.lower() else None
    verdict = age_verdict(asked, minimum, guardian_below=guardian_below)
    if verdict == "denied":
        return f"No. {sentence}"
    if verdict == "guardian":
        return f"Yes, with a parent or guardian to sign the rental. {sentence}"
    return f"Yes. {sentence}"


def _label_matches(question: str, label: str) -> bool:
    lowered = question.lower()
    label = label.lower()
    if any(word in lowered for word in ("e-bike", "ebike", "electric")) and (
        "electric" in label or "e-bike" in label
    ):
        return True
    if "kid" in lowered and "kid" in label:
        return True
    if "city" in lowered and "city" in label and "kid" not in lowered:
        return True
    return False


def deposit_answer(question: str, passages: list[dict]) -> str | None:
    if "deposit" not in question.lower():
        return None
    for passage in passages:
        if "deposit" not in passage["heading"].lower():
            continue
        for raw in passage["text"].splitlines():
            cells = _cells(raw)
            if len(cells) < 2:
                continue
            if _label_matches(question, cells[0]):
                return f"The deposit for a {cells[0].lower()} is {cells[1]} per bike."
    return contact_staff_message()


def location_answer(question: str, passages: list[dict]) -> str | None:
    lowered = question.lower()
    if not any(phrase in lowered for phrase in ("where are you", "where is the shop", "your address")):
        return None
    for passage in passages:
        if "where" in passage["heading"].lower():
            body = _body(passage)
            if body:
                return body
    return contact_staff_message()


def weather_answer(question: str, passages: list[dict]) -> str | None:
    if not _is_weather(question):
        return None
    blob = "\n".join(passage["text"] for passage in passages)
    marker = "The staff decide each case."
    if marker not in blob or not storm_needs_staff(blob):
        return contact_staff_message()
    follow = "Customers have to contact the staff for this."
    quoted = marker + ((" " + follow) if follow in blob else "")
    return (
        "No. A storm warning does not automatically give a full refund. "
        f"{quoted} Contact the staff at {STAFF_EMAIL} or {STAFF_PHONE}."
    )


def late_answer(question: str, passages: list[dict]) -> str | None:
    if "late" not in question.lower():
        return None
    for passage in passages:
        if "late" in passage["heading"].lower():
            body = _body(passage)
            if body:
                return body
    return contact_staff_message()


def generic_answer(question: str, passages: list[dict]) -> str | None:
    tokens = [token for token in _words(question) if token not in {"bike", "bikes"}]
    best_body = None
    best_score = 0
    for passage in passages:
        body = _body(passage)
        if not body:
            continue
        haystack = (body + " " + passage["heading"]).lower()
        score = sum(1 for token in tokens if token in haystack)
        if score > best_score:
            best_score = score
            best_body = body
    if not best_body or best_score == 0:
        return None
    lines = [line for line in best_body.splitlines() if line.strip()]
    return "From the CityWheels policies: " + " ".join(lines[:4])


def compose_policy_answer(question: str, payload: dict) -> str:
    passages = payload.get("passages") or []
    if not passages:
        return contact_staff_message()
    for handler in (
        hours_answer,
        day_price_answer,
        age_answer,
        deposit_answer,
        location_answer,
        late_answer,
        weather_answer,
        generic_answer,
    ):
        answer = handler(question, passages)
        if answer:
            return answer
    return contact_staff_message()


def format_refund(payload: dict) -> str:
    if payload.get("error") and not payload.get("can_cancel") and payload.get("refund_eur") is None:
        return contact_staff_message()
    if not payload.get("found"):
        return booking_not_found_message(str(payload.get("id") or ""))
    if not payload.get("can_cancel"):
        return (
            f"Booking {payload['id']} has status {payload['status']} and cannot be cancelled. "
            "Nothing is refunded, including the deposit. "
            f"The refund is {payload['refund_eur']} EUR."
        )
    when = "now"
    if payload.get("as_of"):
        when = "on " + datetime.fromisoformat(payload["as_of"]).strftime("%Y-%m-%d %H:%M") + " shop time"
    return (
        f"If you cancel booking {payload['id']} {when}, you get {payload['refund_eur']} EUR back. "
        f"That is {payload['rental_refund_eur']} EUR of the rental price "
        f"plus the {payload['deposit_refund_eur']} EUR deposit. "
        f"{payload['booking_fee_note']} {payload['channel_detail']}"
    )


def format_booking(payload: dict) -> str:
    if not payload.get("found"):
        return booking_not_found_message(str(payload.get("id") or ""))
    booking = payload["booking"]
    add_ons = booking.get("add_ons") or []
    add_on_text = ", ".join(add_ons) if add_ons else "none"
    return (
        f"Booking {booking['id']} is {booking['status']}. "
        f"Bike type: {booking['bike_type']}. Bikes: {booking['bikes']}. "
        f"Start: {booking['start']}. End: {booking['end']}. "
        f"Add-ons: {add_on_text}. "
        f"Rental price: {booking['rental_price_eur']} EUR. "
        f"Booking fee: {booking['booking_fee_eur']} EUR. "
        f"Deposit: {booking['deposit_eur']} EUR. "
        f"Paid with: {booking['paid_with']}."
    )


def change_answer(question: str, parsed: list[tuple[str, dict]]) -> str | None:
    if not _is_change(question):
        return None
    booking_payload = next((payload for name, payload in parsed if name == "get_booking"), None)
    policy_payload = next((payload for name, payload in parsed if name == "search_policies"), None)
    if booking_payload is None or policy_payload is None:
        return None
    blob = "\n".join(passage.get("text", "") for passage in policy_payload.get("passages") or [])
    rule = "Later changes are not possible: the customer has to cancel and book again."
    if rule not in blob:
        return contact_staff_message()
    if not booking_payload.get("found"):
        return booking_not_found_message(str(booking_payload.get("id") or ""))
    booking = booking_payload["booking"]
    start = datetime.fromisoformat(str(booking["start"]))
    allowed = change_allowed(status=str(booking.get("status")), start=start, now=shop_now())
    if not allowed:
        return (
            f"No. Booking {booking['id']} is {booking['status']} and cannot be changed for free now. "
            f"{rule}"
        )
    return (
        f"Yes, booking {booking['id']} can be changed for free up to 24 hours before the start, "
        "if bikes are available. "
        f"{rule}"
    )


def _answer_from_tools(question: str, tool_messages: list[dict]) -> str:
    parsed = []
    for message in tool_messages:
        try:
            payload = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            return contact_staff_message()
        parsed.append((message.get("name"), payload))
    changed = change_answer(question, parsed)
    if changed:
        return changed
    requested = any(name == "request_cancellation" and "write_intent" in payload for name, payload in parsed)
    for name, payload in parsed:
        if name == "calculate_refund":
            quote = format_refund(payload)
            if requested and payload.get("can_cancel"):
                return f"{quote} Nothing has changed yet. You will get an exact quote to confirm."
            return quote
    for name, payload in parsed:
        if name == "get_booking":
            return format_booking(payload)
    for name, payload in parsed:
        if name == "search_policies":
            return compose_policy_answer(question, payload)
    return contact_staff_message()


class FakeModel:
    """Scripted model used by tests, the offline CLI, and evaluation."""

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        del tools
        question = _user_question(messages)
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if tool_messages:
            return ModelTurn(content=_answer_from_tools(question, tool_messages))
        decisions = _decide(question)
        if not decisions:
            return ModelTurn(content=contact_staff_message())
        return ModelTurn(
            tool_calls=[
                ToolCall(id=f"call-{index}", name=name, arguments=arguments)
                for index, (name, arguments) in enumerate(decisions, start=1)
            ]
        )
