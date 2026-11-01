"""Shop decisions that need a clock and policy text, not a file or a network call."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from citywheels.clock import WARSAW

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_SPAN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December) to "
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)",
    re.IGNORECASE,
)
_HOURS = re.compile(r"\d{2}:\d{2}[–—-]\d{2}:\d{2}")


def aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=WARSAW)
    return moment


def change_allowed(*, status: str, start: datetime, now: datetime) -> bool:
    """A confirmed booking can be changed until 24 hours before the start, inclusive."""
    if status != "confirmed":
        return False
    return aware(start) - aware(now) >= timedelta(hours=24)


def _season_contains(line: str, month: int) -> bool:
    match = _MONTH_SPAN.search(line)
    if not match:
        return False
    start = _MONTHS[match.group(1).lower()]
    end = _MONTHS[match.group(2).lower()]
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


@dataclass(frozen=True)
class HoursLine:
    line: str
    hours: str


def applicable_hours(policy_text: str, moment: datetime) -> HoursLine | None:
    """The opening line whose season contains `moment`. The months come from the text."""
    month = aware(moment).month
    for raw in policy_text.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        found = _HOURS.search(line)
        if found and _season_contains(line, month):
            return HoursLine(line=line, hours=found.group(0))
    return None


def age_verdict(asked: int, minimum: int, *, guardian_below: int | None) -> str:
    """`denied`, `guardian`, or `allowed`."""
    if asked < minimum:
        return "denied"
    if guardian_below is not None and asked < guardian_below:
        return "guardian"
    return "allowed"


def range_verdict(asked: int, low: int, high: int) -> str:
    return "allowed" if low <= asked <= high else "denied"


def storm_needs_staff(policy_text: str) -> bool:
    """True when the policy text says the staff decide a storm refund."""
    return "staff decide" in policy_text.lower()
