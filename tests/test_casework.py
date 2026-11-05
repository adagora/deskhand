import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from casework.backend import Booking, SqliteBackend, booking_from_shop_record
from casework.desk import Desk
from casework.grants import GrantBook
from casework.identity import Directory, token_digest
from casework.model import CANCEL_REFUND, Consent, CustomerScope, Principal, Proposal, Refusal, Review
from casework.staff import StaffQueue
from casework.store import transaction
from citywheels.bookings import load_bookings
from citywheels.cases import calculate_refund
from citywheels.clock import FIXED_NOW

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
INTENT = "citywheels.cancellation"
RELEASE = "release-1"
ACTOR = Principal("shop-a", "alice", ["reviewer", "deployer", "operator"])
SCOPE = CustomerScope("shop-a", "customer-a")


def booking(**changes) -> Booking:
    values = dict(
        id="CW-1002", tenant="shop-a", customer="customer-a", version=1, status="confirmed",
        starts_at="2026-11-05T00:00:00+00:00", rental_minor=8000, deposit_minor=30000, booking_fee_minor=200,
        currency="EUR", payment_ref="original-card", payment_kind="card",
    )
    values.update(changes)
    return Booking(**values)


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Fixture:
    def __init__(self, root: Path, *bookings: Booking, tier: str = "transact", business_clock=None, auto_approve_minor=10000):
        self.root = root
        self.backend = SqliteBackend(root / "backend.db")
        self.backend.seed(list(bookings) or [booking()])
        self.grants = GrantBook(root / "desk.db")
        self.clock = Clock(NOW)
        self.release = RELEASE
        self.desk = Desk(
            self.backend, self.grants, release=lambda: self.release, clock=self.clock,
            business_clock=business_clock or self.clock, auto_approve_minor=auto_approve_minor,
            quote_ttl=timedelta(minutes=5),
        )
        self.grant_id = self.issue_grant(tier)

    def issue_grant(self, tier: str) -> str:
        grant_id = self.grants.approve(
            ACTOR, intent=INTENT, tier=tier, actions=[CANCEL_REFUND], max_amount_minor=34000, currency="EUR",
            expires_at=(NOW + timedelta(hours=1)).isoformat(), release=RELEASE, now=NOW,
        )
        self.grants.activate(ACTOR, grant_id, RELEASE, NOW)
        return grant_id

    def propose(self, scope: CustomerScope = SCOPE) -> Proposal:
        return self.desk.propose(scope, "case-1", "CW-1002", INTENT)

    def authority(self, proposal: Proposal, verdict: str = "approved") -> tuple[Consent, Review]:
        digest = proposal.digest()
        at = self.clock.now.isoformat()
        return Consent(proposal.customer, digest, at), Review("alice", verdict, digest, at)

    def run_to_settled(self, proposal: Proposal) -> None:
        consent, review = self.authority(proposal)
        self.desk.recheck(proposal, consent, review)
        self.desk.cancel(proposal)
        self.desk.refund(proposal)


class CaseworkTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def assertRefused(self, code: str, call, *args, **kwargs):
        with self.assertRaises(Refusal) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)


class ProposalTests(CaseworkTestCase):
    def test_owned_live_facts_set_integer_money_and_authority_precedes_cancellation(self):
        f = Fixture(self.root)
        proposal = f.propose()
        self.assertEqual(proposal.amount_minor, 34000)
        self.assertTrue(proposal.review_required)
        self.assertEqual(f.backend.owned(SCOPE, "CW-1002").status, "confirmed")
        consent, review = f.authority(proposal)
        self.assertRefused("consent_required", f.desk.recheck, proposal, None, None)
        self.assertRefused("operation_approval_required", f.desk.recheck, proposal, consent, None)
        self.assertEqual(f.desk.recheck(proposal, consent, review), f.grant_id)
        f.desk.cancel(proposal)
        f.desk.refund(proposal)
        observed = f.desk.observe(proposal)
        self.assertEqual(observed.state, "settled")
        self.assertEqual(observed.amount_minor, 34000)
        self.assertEqual(f.backend.owned(SCOPE, "CW-1002").status, "cancelled")
        f.desk.cancel(proposal)
        f.desk.refund(proposal)
        self.assertEqual(f.backend.refund_count(), 1)
        self.assertEqual(f.backend.refunds()[0]["payment_ref"], "original-card")

    def test_a_small_refund_needs_consent_only(self):
        f = Fixture(self.root, booking(deposit_minor=5000))
        proposal = f.propose()
        self.assertEqual(proposal.amount_minor, 9000)
        self.assertFalse(proposal.review_required)
        self.assertRefused("consent_required", f.desk.recheck, proposal, None, None)
        consent, _ = f.authority(proposal)
        self.assertEqual(f.desk.recheck(proposal, consent, None), f.grant_id)

    def test_booking_identifiers_do_not_establish_access(self):
        f = Fixture(self.root)
        for scope in (CustomerScope("shop-b", "customer-a"), CustomerScope("shop-a", "customer-b")):
            self.assertRefused("booking_not_accessible", f.desk.propose, scope, "case-2", "CW-1002", INTENT)
        proposal = f.propose()
        stranger = replace(proposal, customer="customer-b")
        self.assertRefused("booking_not_accessible", f.desk.cancel, stranger)
        self.assertRefused("booking_not_accessible", f.desk.observe, stranger)
        self.assertEqual(f.backend.refund_count(), 0)

    def test_a_storm_claim_goes_to_staff_and_writes_nothing(self):
        f = Fixture(self.root)
        self.assertRefused("staff_required_for_claim", f.desk.propose, SCOPE, "case-1", "CW-1002", INTENT, True)
        self.assertEqual(f.backend.owned(SCOPE, "CW-1002").status, "confirmed")

    def test_a_booking_that_is_not_confirmed_is_not_cancellable(self):
        for status in ("cancelled", "completed", "in_progress"):
            with self.subTest(status=status):
                root = self.root / status
                root.mkdir()
                f = Fixture(root, booking(status=status))
                self.assertRefused("not_cancellable", f.propose)

    def test_an_unknown_payment_route_goes_to_staff(self):
        f = Fixture(self.root, booking(payment_kind="bank-transfer"))
        self.assertRefused("staff_required_for_payment_route", f.propose)


class RecheckTests(CaseworkTestCase):
    def _refused_without_writes(self, code: str, change, verdict: str = "approved", at=None):
        name = code + verdict
        (self.root / name).mkdir()
        f = Fixture(self.root / name)
        proposal = f.propose()
        consent, review = f.authority(proposal, verdict)
        change(f)
        if at is not None:
            f.clock.now = at
        self.assertRefused(code, f.desk.recheck, proposal, consent, review)
        self.assertEqual(f.backend.refund_count(), 0)
        self.assertEqual(f.backend.owned(SCOPE, "CW-1002").status, "confirmed")

    def _change_booking(self, f: Fixture) -> None:
        with transaction(f.backend.path) as connection:
            connection.execute("UPDATE booking SET version=version+1, rental_minor=10000")

    def test_a_rejection_expired_quote_changed_booking_policy_or_grant_never_dispatches(self):
        self._refused_without_writes("operation_rejected", lambda f: None, verdict="rejected")
        self._refused_without_writes("proposal_expired", lambda f: None, at=NOW + timedelta(minutes=5))
        self._refused_without_writes("booking_changed", self._change_booking)
        self._refused_without_writes("policy_changed", lambda f: setattr(f, "release", "release-2"))
        self._refused_without_writes("revoked", lambda f: f.grants.revoke(ACTOR, f.grant_id, "regression", NOW))

    def test_a_refund_band_change_during_the_wait_refuses_the_old_amount(self):
        (self.root / "band").mkdir()
        f = Fixture(self.root / "band")
        proposal = f.propose()
        consent, review = f.authority(proposal)
        business = Clock(NOW + timedelta(hours=20))
        f.desk.business_clock = business
        self.assertRefused("amount_changed", f.desk.recheck, proposal, consent, review)
        self.assertEqual(f.backend.refund_count(), 0)

    def test_shadow_assist_and_contain_grants_cannot_write(self):
        for tier in ("shadow", "assist", "contain"):
            with self.subTest(tier=tier):
                (self.root / tier).mkdir()
                f = Fixture(self.root / tier, tier=tier)
                proposal = f.propose()
                self.assertRefused("tier_cannot_transact", f.desk.recheck, proposal, *f.authority(proposal))
                self.assertEqual(f.backend.owned(SCOPE, "CW-1002").status, "confirmed")

    def test_a_clock_before_the_quote_fails_closed(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, review = f.authority(proposal)
        f.clock.now = NOW - timedelta(seconds=1)
        self.assertRefused("clock_regressed", f.desk.recheck, proposal, consent, review)

    def test_consent_and_review_bind_every_field_of_the_proposal(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, review = f.authority(proposal)
        for item in fields(Proposal):
            value = getattr(proposal, item.name)
            changed = not value if isinstance(value, bool) else value + 1 if isinstance(value, int) else value + "x"
            altered = replace(proposal, **{item.name: changed})
            with self.subTest(field=item.name):
                self.assertRefused("consent_required", f.desk.recheck, altered, consent, review)
        self.assertEqual(f.backend.refund_count(), 0)

    def test_a_reviewer_cannot_be_the_customer(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, _ = f.authority(proposal)
        own_review = Review(proposal.customer, "approved", proposal.digest(), NOW.isoformat())
        self.assertRefused("operation_approval_required", f.desk.recheck, proposal, consent, own_review)

    def test_a_new_quote_after_expiry_needs_new_consent_and_review(self):
        f = Fixture(self.root)
        old = f.propose()
        consent, review = f.authority(old)
        f.clock.now = NOW + timedelta(minutes=5)
        self.assertRefused("proposal_expired", f.desk.recheck, old, consent, review)
        new = f.propose()
        self.assertEqual(new.id, old.id)
        self.assertNotEqual(new.digest(), old.digest())
        self.assertRefused("consent_required", f.desk.recheck, new, consent, review)
        fresh_consent, fresh_review = f.authority(new)
        self.assertEqual(f.desk.recheck(new, fresh_consent, fresh_review), f.grant_id)

    def test_the_shop_clock_does_not_weaken_system_expiry_and_a_voucher_stays_a_voucher(self):
        shop = datetime(2026, 6, 10, 9, 0, tzinfo=timezone(timedelta(hours=2)))
        record = booking(starts_at="2026-06-11T18:00:00+02:00", payment_kind="voucher")
        f = Fixture(self.root, record, business_clock=lambda: shop)
        proposal = f.propose()
        self.assertEqual(proposal.amount_minor, 34000)
        self.assertEqual(proposal.business_at, shop.isoformat())
        self.assertEqual(proposal.created_at, NOW.isoformat())
        self.assertEqual(proposal.payment_kind, "voucher")
        f.run_to_settled(proposal)
        self.assertEqual(f.backend.refunds()[0]["payment_kind"], "voucher")
        f.clock.now = NOW + timedelta(minutes=5)
        self.assertRefused("proposal_expired", f.desk.recheck, proposal, *f.authority(proposal))


class RecoveryTests(CaseworkTestCase):
    def test_a_crash_before_or_after_commit_reconciles_without_a_second_refund(self):
        f = Fixture(self.root)
        proposal = f.propose()
        f.desk.recheck(proposal, *f.authority(proposal))
        self.assertEqual(f.desk.observe(proposal).state, "none")
        f.desk.cancel(proposal)
        reopened = SqliteBackend(self.root / "backend.db")
        self.assertEqual(reopened.observe(proposal).state, "cancelled-only")
        f.desk.refund(proposal)
        reopened = SqliteBackend(self.root / "backend.db")
        self.assertEqual(reopened.observe(proposal).state, "settled")
        f.desk.cancel(proposal)
        f.desk.refund(proposal)
        self.assertEqual(reopened.refund_count(), 1)

    def test_backend_deduplication_refuses_conflicting_refund_amounts(self):
        f = Fixture(self.root)
        proposal = f.propose()
        f.run_to_settled(proposal)
        self.assertRefused("idempotency_conflict", f.desk.refund, replace(proposal, amount_minor=34001))
        self.assertEqual(f.backend.refund_count(), 1)

    def test_a_refund_without_its_own_cancellation_is_refused(self):
        f = Fixture(self.root)
        proposal = f.propose()
        self.assertRefused("cancellation_not_observed", f.desk.refund, proposal)
        self.assertEqual(f.backend.refund_count(), 0)

    def test_a_changed_booking_is_observed_as_changed(self):
        f = Fixture(self.root)
        proposal = f.propose()
        other = replace(proposal, id="other-operation")
        f.backend.cancel(other)
        self.assertEqual(f.desk.observe(proposal).state, "changed")
        self.assertRefused("booking_changed", f.desk.cancel, proposal)

    def _damage_ledger(self, f: Fixture) -> None:
        with transaction(f.backend.path) as connection:
            connection.execute("UPDATE refund SET amount_minor=amount_minor+1")

    def test_an_inconsistent_read_back_cannot_authorize_another_refund(self):
        f = Fixture(self.root)
        proposal = f.propose()
        f.run_to_settled(proposal)
        self._damage_ledger(f)
        observed = f.desk.observe(proposal)
        self.assertEqual((observed.state, observed.code), ("mismatch", "readback_mismatch"))
        consent, review = f.authority(proposal)
        self.assertRefused("readback_mismatch", f.desk.recheck_resolution, ACTOR, proposal, consent, review)
        self.assertEqual(f.backend.refund_count(), 1)

    def test_partial_completion_needs_a_supervisor_and_current_authority(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, review = f.authority(proposal)
        f.desk.recheck(proposal, consent, review)
        f.desk.cancel(proposal)
        self.assertRefused(
            "resolution_forbidden", f.desk.recheck_resolution, replace(ACTOR, roles=["operator"]), proposal, consent, review
        )
        self.assertRefused(
            "resolution_forbidden", f.desk.recheck_resolution, replace(ACTOR, tenant="other"), proposal, consent, review
        )
        f.clock.now = NOW + timedelta(minutes=10)
        self.assertEqual(f.desk.recheck_resolution(ACTOR, proposal, consent, review), f.grant_id)
        f.desk.refund(proposal)
        self.assertEqual(f.desk.observe(proposal).state, "settled")
        self.assertRefused("no_committed_cancellation", f.desk.recheck_resolution, ACTOR, proposal, consent, review)
        self.assertEqual(f.backend.refund_count(), 1)

    def test_a_revoked_grant_cannot_settle_a_new_refund(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, review = f.authority(proposal)
        f.desk.recheck(proposal, consent, review)
        f.desk.cancel(proposal)
        f.grants.revoke(ACTOR, f.grant_id, "regression", NOW)
        self.assertRefused("revoked", f.desk.recheck_resolution, ACTOR, proposal, consent, review)
        self.assertEqual(f.backend.refund_count(), 0)

    def test_resolution_needs_the_original_consent(self):
        f = Fixture(self.root)
        proposal = f.propose()
        consent, review = f.authority(proposal)
        f.desk.recheck(proposal, consent, review)
        f.desk.cancel(proposal)
        self.assertRefused("consent_required", f.desk.recheck_resolution, ACTOR, proposal, None, review)

    def test_a_revoked_grant_leaves_a_settled_refund_in_place(self):
        f = Fixture(self.root)
        proposal = f.propose()
        f.run_to_settled(proposal)
        before = f.backend.refunds()
        f.grants.revoke(ACTOR, f.grant_id, "regression", NOW)
        self.assertEqual(f.backend.refunds(), before)
        self.assertEqual(f.desk.observe(proposal).state, "settled")

    def test_three_real_processes_cannot_cancel_or_refund_the_same_operation_twice(self):
        f = Fixture(self.root)
        proposal = f.propose()
        (self.root / "proposal.json").write_text(json.dumps(asdict(proposal)))
        code = """
import json, sys
from pathlib import Path
root, repository = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, repository)
from casework.backend import SqliteBackend
from casework.model import Proposal, Refusal
proposal = Proposal(**json.loads((root / "proposal.json").read_text()))
backend = SqliteBackend(root / "backend.db")
sys.stdout.write("ready\\n"); sys.stdout.flush()
sys.stdin.readline()
try:
    backend.cancel(proposal)
    backend.refund(proposal, "2026-11-03T12:00:00+00:00")
    print(backend.observe(proposal).state)
except Refusal as refusal:
    print(refusal.code)
"""
        children = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(self.root), str(ROOT)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(3)
        ]
        for child in children:
            self.assertEqual(child.stdout.readline().strip(), "ready")
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = [child.communicate(timeout=60) for child in children]
        self.assertEqual([child.returncode for child in children], [0, 0, 0], results)
        self.assertEqual({out.strip() for out, _ in results}, {"settled"})
        self.assertEqual(f.backend.refund_count(), 1)


class MoneyRuleTests(CaseworkTestCase):
    def test_the_desk_quotes_what_calculate_refund_quotes_on_every_booking(self):
        records = load_bookings()
        self.assertEqual(len(records), 10)
        bookings = [booking_from_shop_record(record, "shop-a", "customer-a") for record in records]
        f = Fixture(self.root, *bookings, business_clock=lambda: FIXED_NOW)
        for record in records:
            with self.subTest(booking=record["id"]):
                quoted = calculate_refund(record["id"])
                if record["status"] != "confirmed":
                    self.assertRefused("not_cancellable", f.desk.propose, SCOPE, "case-1", record["id"], INTENT)
                    continue
                proposal = f.desk.propose(SCOPE, "case-1", record["id"], INTENT)
                self.assertEqual(proposal.amount_minor, int(Decimal(str(quoted["refund_eur"])) * 100))

    def test_the_payment_route_comes_from_the_booking_and_not_from_the_proposal(self):
        f = Fixture(self.root)
        proposal = f.propose()
        self.assertNotIn("payment_ref", asdict(proposal))
        f.run_to_settled(proposal)
        self.assertEqual(f.backend.refunds()[0]["payment_ref"], "original-card")


class GrantTests(CaseworkTestCase):
    def _approved(self, grants: GrantBook, **changes) -> str:
        values = dict(
            intent=INTENT, tier="transact", actions=[CANCEL_REFUND], max_amount_minor=100, currency="EUR",
            expires_at=(NOW + timedelta(hours=1)).isoformat(), release=RELEASE, now=NOW,
        )
        values.update(changes)
        return grants.approve(ACTOR, **values)

    def _authorize(self, grants: GrantBook, **changes):
        values = dict(
            tenant="shop-a", intent=INTENT, action=CANCEL_REFUND, amount_minor=100, currency="EUR", release=RELEASE, now=NOW,
        )
        values.update(changes)
        return grants.authorize(**values)

    def test_approval_and_activation_are_separate_steps_with_their_own_roles(self):
        grants = GrantBook(self.root / "desk.db")
        self.assertRefused("forbidden", self._approved_by, grants, replace(ACTOR, roles=["deployer"]))
        grant_id = self._approved(grants)
        self.assertRefused("not_active", self._authorize, grants)
        self.assertRefused("forbidden", grants.activate, replace(ACTOR, roles=["reviewer"]), grant_id, RELEASE, NOW)
        grants.activate(ACTOR, grant_id, RELEASE, NOW)
        self.assertEqual(self._authorize(grants).id, grant_id)

    def _approved_by(self, grants: GrantBook, principal: Principal):
        return grants.approve(
            principal, intent=INTENT, tier="transact", actions=[CANCEL_REFUND], max_amount_minor=100, currency="EUR",
            expires_at=(NOW + timedelta(hours=1)).isoformat(), release=RELEASE, now=NOW,
        )

    def test_limits_expiry_release_and_scope_are_checked_on_each_authorization(self):
        grants = GrantBook(self.root / "desk.db")
        grants.activate(ACTOR, self._approved(grants), RELEASE, NOW)
        self.assertRefused("limits", self._authorize, grants, amount_minor=101)
        self.assertRefused("limits", self._authorize, grants, currency="USD")
        self.assertRefused("limits", self._authorize, grants, action="deliver-answer")
        self.assertRefused("release_changed", self._authorize, grants, release="release-2")
        self.assertRefused("expired", self._authorize, grants, now=NOW + timedelta(hours=1))
        self.assertRefused("not_active", self._authorize, grants, tenant="shop-b")

    def test_a_revoked_grant_stops_new_authorization_and_cannot_be_activated(self):
        grants = GrantBook(self.root / "desk.db")
        grant_id = self._approved(grants)
        grants.activate(ACTOR, grant_id, RELEASE, NOW)
        grants.revoke(ACTOR, grant_id, "regression", NOW)
        self.assertRefused("revoked", self._authorize, grants)
        self.assertRefused("revoked", grants.activate, ACTOR, grant_id, RELEASE, NOW)


class StaffQueueTests(CaseworkTestCase):
    def test_a_case_keeps_one_open_item_per_code_until_a_person_closes_it(self):
        queue = StaffQueue(self.root / "desk.db")
        first = queue.open("shop-a", "case-1", "op-1", "cancelled_refund_unsettled", "refund-ops", NOW.isoformat())
        again = queue.open("shop-a", "case-1", "op-1", "cancelled_refund_unsettled", "refund-ops", NOW.isoformat())
        self.assertEqual(first.id, again.id)
        self.assertEqual([item.code for item in queue.items("shop-a", "open")], ["cancelled_refund_unsettled"])
        self.assertEqual(queue.items("shop-b"), [])
        queue.close("shop-a", "case-1", "alice", "manual-refund", NOW.isoformat())
        self.assertEqual(queue.items("shop-a", "open"), [])
        self.assertEqual(queue.items("shop-a", "closed")[0].closed_by, "alice")


class IdentityTests(unittest.TestCase):
    def test_a_token_selects_the_principal_and_nothing_in_a_request_can(self):
        directory = Directory(
            [{"sha256": token_digest("secret-a"), "tenant": "shop-a", "id": "customer-a", "roles": ["customer"]}]
        )
        self.assertEqual(directory.authenticate("secret-a"), Principal("shop-a", "customer-a", ["customer"]))
        for token in ("", "secret-b", None):
            with self.assertRaises(Refusal):
                directory.authenticate(token)


if __name__ == "__main__":
    unittest.main()
