"""Fixed shop clock for CityWheels answers.

The bookings are written for June 2026. Reading the machine clock would change
refunds and opening hours from one day to the next, so every answer uses this
instant instead.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
# 2026-06-10 09:00 Europe/Warsaw (UTC+02:00).
FIXED_NOW = datetime(2026, 6, 10, 9, 0, tzinfo=WARSAW)


def shop_now() -> datetime:
    """The only clock the refund code should read when the caller does not pass one."""
    return FIXED_NOW
