import json
from pathlib import Path

from citywheels.paths import default_data_dir


def load_bookings(data_dir: Path | None = None) -> list[dict]:
    directory = data_dir or default_data_dir()
    path = directory / "bookings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a list of bookings")
    return data


def get_booking(booking_id: str, data_dir: Path | None = None) -> dict:
    """Return one stored booking, or a not-found result. Never raises for an unknown id."""
    normalized = str(booking_id or "").strip().upper()
    if not normalized:
        return {
            "found": False,
            "id": "",
            "message": "Booking was not found.",
        }
    for booking in load_bookings(data_dir):
        stored_id = str(booking.get("id", "")).upper()
        if stored_id == normalized:
            return {"found": True, "booking": booking}
    return {
        "found": False,
        "id": normalized,
        "message": f"Booking {normalized} was not found.",
    }
