STAFF_EMAIL = "help@citywheels.example"
STAFF_PHONE = "+48 000 000 000"


def contact_staff_message() -> str:
    return (
        "I can't answer that from the CityWheels policies or bookings. "
        f"Please contact the staff at {STAFF_EMAIL} or {STAFF_PHONE}."
    )


def booking_not_found_message(booking_id: str) -> str:
    shown = booking_id or "that booking"
    return (
        f"Booking {shown} was not found. "
        f"Please contact the staff at {STAFF_EMAIL} or {STAFF_PHONE}."
    )
