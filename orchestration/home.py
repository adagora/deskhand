import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from casework.backend import SqliteBackend, booking_from_shop_record
from casework.desk import Desk, system_now
from casework.evidence import evidence_from_environment
from casework.grants import GrantBook
from casework.identity import Directory, token_digest
from casework.model import CANCEL_REFUND, Principal
from casework.rule import current_release
from casework.staff import StaffQueue
from citywheels.bookings import load_bookings
from orchestration.activities import CaseActivities
from orchestration.gateway import TASK_QUEUE
from orchestration.workflow import CancelRefund

TENANT = "citywheels"
INTENT = "citywheels.cancellation"
_IDENTITIES = {
    "customer": ("demo-customer", ["customer"]),
    "reviewer": ("demo-reviewer", ["reviewer"]),
    "supervisor": ("demo-supervisor", ["reviewer", "operator", "deployer"]),
}


@dataclass(frozen=True)
class Home:
    root: Path
    backend: SqliteBackend
    grants: GrantBook
    staff: StaffQueue

    @property
    def directory(self) -> Directory:
        return Directory.from_file(self.root / "identities.json")

    def desk(self, **overrides) -> Desk:
        return Desk(self.backend, self.grants, **overrides)


def open_home(root: Path) -> Home:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return Home(root, SqliteBackend(root / "backend.db"), GrantBook(root / "desk.db"), StaffQueue(root / "desk.db"))


def seed_home(root: Path, data_dir: Path | None = None) -> tuple[Home, dict[str, str]]:
    home = open_home(root)
    records = load_bookings(data_dir)
    home.backend.seed([booking_from_shop_record(record, TENANT, "demo-customer") for record in records])
    tokens = {name: secrets.token_urlsafe(32) for name in _IDENTITIES}
    entries = [
        {"sha256": token_digest(tokens[name]), "tenant": TENANT, "id": identity, "roles": roles}
        for name, (identity, roles) in _IDENTITIES.items()
    ]
    identities = home.root / "identities.json"
    identities.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.chmod(identities, 0o600)
    now = system_now()
    supervisor = Principal(TENANT, "demo-supervisor", ["reviewer", "operator", "deployer"])
    grant_id = home.grants.approve(
        supervisor,
        intent=INTENT,
        tier="transact",
        actions=[CANCEL_REFUND],
        max_amount_minor=100000,
        currency="EUR",
        expires_at=(now + timedelta(days=90)).isoformat(),
        release=current_release(),
        now=now,
    )
    home.grants.activate(supervisor, grant_id, current_release(), now)
    return home, tokens


def build_worker(client: Client, activities: CaseActivities, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[CancelRefund],
        activities=activities.registered(),
        activity_executor=ThreadPoolExecutor(max_workers=8),
        graceful_shutdown_timeout=timedelta(seconds=5),
    )


def activities_for(home: Home, **desk_overrides) -> CaseActivities:
    return CaseActivities(home.desk(**desk_overrides), home.staff, evidence_from_environment())
