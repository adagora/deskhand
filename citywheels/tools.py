import json
from datetime import datetime
from pathlib import Path

from citywheels.bookings import BookingView, get_booking
from citywheels.policies import search_policies
from citywheels.cases import calculate_refund, request_cancellation

TOOL_SPECS = [
    {
        "name": "search_policies",
        "description": (
            "Find verbatim passages in the CityWheels policy documents "
            "(prices, cancellations, and FAQ) that relate to a question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search words, usually the customer question.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_booking",
        "description": (
            "Return the stored fields of one booking from bookings.json, "
            "or a not-found result. Does not invent bookings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "Booking id such as CW-1002.",
                }
            },
            "required": ["booking_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_refund",
        "description": (
            "Calculate the refund if the customer cancels this booking at the "
            "fixed CityWheels clock. The amount is computed by code. Do not "
            "calculate a refund yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "Booking id such as CW-1002.",
                },
                "at": {
                    "type": "string",
                    "description": (
                        "Only for a what-if question about a later time: ISO 8601 date and time "
                        "with offset, such as 2026-06-11T09:00:00+02:00. Leave it out to price "
                        "cancelling now. It cannot be earlier than now."
                    ),
                },
            },
            "required": ["booking_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_cancellation",
        "description": (
            "Record that the customer clearly asks to cancel this booking now. "
            "This changes nothing: the customer gets an exact quote and must confirm it. "
            "Do not call it when the customer only asks what a cancellation would cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "Booking id such as CW-1002.",
                }
            },
            "required": ["booking_id"],
            "additionalProperties": False,
        },
    },
]


def _arguments(arguments) -> dict:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be an object")
        return parsed
    raise TypeError("Tool arguments must be an object or a JSON string")


def _moment(value) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("at must be an ISO 8601 date and time such as 2026-06-11T09:00:00+02:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("at must be an ISO 8601 date and time such as 2026-06-11T09:00:00+02:00") from None


def run_tool(
    name: str,
    arguments,
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
    view: BookingView | None = None,
) -> dict:
    try:
        args = _arguments(arguments)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"error": f"Tool arguments were not valid JSON: {exc}"}
    try:
        return _dispatch(name, args, data_dir=data_dir, now=now, view=view)
    except Exception as exc:
        return {"error": f"Tool {name} failed: {type(exc).__name__}: {exc}"}


def _dispatch(
    name: str, args: dict, *, data_dir: Path | None, now: datetime | None, view: BookingView | None
) -> dict:
    if name == "search_policies":
        return search_policies(str(args.get("query", "")), data_dir=data_dir)
    if name == "get_booking":
        return get_booking(str(args.get("booking_id", "")), data_dir=data_dir, view=view)
    if name == "calculate_refund":
        return calculate_refund(
            str(args.get("booking_id", "")),
            data_dir=data_dir,
            now=now,
            at=_moment(args.get("at")),
            view=view,
        )
    if name == "request_cancellation":
        return request_cancellation(str(args.get("booking_id", "")), data_dir=data_dir, view=view)
    return {"error": f"Unknown tool: {name}"}
