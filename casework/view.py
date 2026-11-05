from casework.backend import BookingBackend
from casework.model import CustomerScope, Refusal


class BackendView:
    def __init__(self, backend: BookingBackend, scope: CustomerScope):
        self.backend = backend
        self.scope = scope

    def visible(self, record: dict) -> dict | None:
        try:
            booking = self.backend.owned(self.scope, str(record.get("id", "")))
        except Refusal:
            return None
        return {**record, "status": booking.status}
