import json
from datetime import datetime
from pathlib import Path

from citywheels.bookings import get_booking
from citywheels.policies import search_policies
from citywheels.cases import calculate_refund

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


def run_tool(
    name: str,
    arguments,
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    try:
        args = _arguments(arguments)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"error": f"Tool arguments were not valid JSON: {exc}"}
    try:
        return _dispatch(name, args, data_dir=data_dir, now=now)
    except Exception as exc:
        return {"error": f"Tool {name} failed: {type(exc).__name__}: {exc}"}


def _dispatch(name: str, args: dict, *, data_dir: Path | None, now: datetime | None) -> dict:
    if name == "search_policies":
        return search_policies(str(args.get("query", "")), data_dir=data_dir)
    if name == "get_booking":
        return get_booking(str(args.get("booking_id", "")), data_dir=data_dir)
    if name == "calculate_refund":
        return calculate_refund(
            str(args.get("booking_id", "")),
            data_dir=data_dir,
            now=now,
        )
    return {"error": f"Unknown tool: {name}"}
