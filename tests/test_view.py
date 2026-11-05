import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from casework.backend import SqliteBackend, booking_from_shop_record
from casework.model import CustomerScope
from casework.view import BackendView
from citywheels.agent import run_agent
from citywheels.bookings import get_booking, load_bookings
from citywheels.cases import calculate_refund, request_cancellation
from citywheels.fake_model import FakeModel

TENANT = "citywheels"
ALICE = CustomerScope(TENANT, "alice")
BOB = CustomerScope(TENANT, "bob")


def shop_record(booking_id: str) -> dict:
    return next(record for record in load_bookings() if record["id"] == booking_id)


class BackendViewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backend = SqliteBackend(Path(folder.name) / "backend.db")
        self.backend.seed(
            [
                booking_from_shop_record(shop_record("CW-1002"), TENANT, "alice"),
                booking_from_shop_record(shop_record("CW-1001"), TENANT, "bob"),
            ]
        )
        self.alice = BackendView(self.backend, ALICE)

    def test_a_customer_reads_their_own_booking(self):
        found = get_booking("CW-1002", view=self.alice)
        self.assertTrue(found["found"])
        self.assertEqual(found["booking"]["bike_type"], shop_record("CW-1002")["bike_type"])

    def test_another_customers_booking_looks_like_an_unknown_one(self):
        hidden = get_booking("CW-1001", view=self.alice)
        unknown = get_booking("CW-9999", view=self.alice)
        self.assertFalse(hidden["found"])
        self.assertEqual(hidden["message"], "Booking CW-1001 was not found.")
        self.assertEqual(hidden["message"].replace("CW-1001", "CW-9999"), unknown["message"])

    def test_the_refund_and_cancellation_tools_obey_the_same_view(self):
        self.assertIsNone(calculate_refund("CW-1001", view=self.alice)["refund_eur"])
        self.assertFalse(request_cancellation("CW-1001", view=self.alice)["found"])
        self.assertEqual(calculate_refund("CW-1002", view=self.alice)["refund_eur"], 340)
        self.assertEqual(request_cancellation("CW-1002", view=self.alice)["write_intent"]["booking_id"], "CW-1002")

    def test_the_agent_opens_no_case_for_a_booking_that_is_not_the_customers(self):
        result = run_agent("Please cancel booking CW-1001.", FakeModel(), view=self.alice)
        self.assertEqual(result.write_intents, [])
        self.assertIn("was not found", result.answer)

    def test_a_cancelled_booking_in_the_backend_is_not_quoted_from_the_file(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        backend = SqliteBackend(Path(folder.name) / "other.db")
        backend.seed([booking_from_shop_record({**shop_record("CW-1002"), "status": "cancelled"}, TENANT, "alice")])
        quote = calculate_refund("CW-1002", view=BackendView(backend, ALICE))
        self.assertEqual(quote["refund_eur"], 0)
        self.assertEqual(quote["status"], "cancelled")
        self.assertFalse(quote["can_cancel"])


if __name__ == "__main__":
    unittest.main()
