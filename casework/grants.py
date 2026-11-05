import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from casework.model import Principal, Refusal
from casework.store import reading, transaction

TIERS = ("shadow", "assist", "contain", "transact")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS grant_record (
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, intent TEXT NOT NULL, tier TEXT NOT NULL, actions TEXT NOT NULL,
  max_amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, expires_at TEXT NOT NULL, release TEXT NOT NULL,
  reviewer TEXT NOT NULL, approved_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, revoke_reason TEXT
);
CREATE TABLE IF NOT EXISTS active_grant (
  tenant TEXT NOT NULL, intent TEXT NOT NULL, grant_id TEXT NOT NULL, activated_by TEXT NOT NULL,
  activated_at TEXT NOT NULL, PRIMARY KEY (tenant, intent)
);
"""


@dataclass(frozen=True)
class Grant:
    id: str
    tenant: str
    intent: str
    tier: str
    actions: list[str]
    max_amount_minor: int
    currency: str
    expires_at: str
    release: str


def _require(principal: Principal, role: str) -> None:
    if role not in principal.roles:
        raise Refusal("forbidden")


class GrantBook:
    def __init__(self, path: Path):
        self.path = Path(path)
        with transaction(self.path) as connection:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)

    def approve(
        self,
        principal: Principal,
        *,
        intent: str,
        tier: str,
        actions: list[str],
        max_amount_minor: int,
        currency: str,
        expires_at: str,
        release: str,
        now: datetime,
    ) -> str:
        _require(principal, "reviewer")
        if tier not in TIERS or max_amount_minor < 0 or datetime.fromisoformat(expires_at) <= now:
            raise Refusal("invalid_grant")
        grant_id = str(uuid4())
        with transaction(self.path) as connection:
            connection.execute(
                "INSERT INTO grant_record (id, tenant, intent, tier, actions, max_amount_minor, currency, expires_at,"
                " release, reviewer, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant_id, principal.tenant, intent, tier, json.dumps(actions), max_amount_minor, currency,
                    expires_at, release, principal.id, now.isoformat(),
                ),
            )
        return grant_id

    def activate(self, principal: Principal, grant_id: str, release: str, now: datetime) -> None:
        _require(principal, "deployer")
        with transaction(self.path) as connection:
            row = self._usable(connection, principal.tenant, grant_id, now)
            if row["release"] != release:
                raise Refusal("release_changed")
            connection.execute(
                "INSERT OR REPLACE INTO active_grant (tenant, intent, grant_id, activated_by, activated_at)"
                " VALUES (?,?,?,?,?)",
                (row["tenant"], row["intent"], grant_id, principal.id, now.isoformat()),
            )

    def revoke(self, principal: Principal, grant_id: str, reason: str, now: datetime) -> None:
        _require(principal, "reviewer")
        with transaction(self.path) as connection:
            changed = connection.execute(
                "UPDATE grant_record SET revoked_at=COALESCE(revoked_at, ?), revoked_by=COALESCE(revoked_by, ?),"
                " revoke_reason=COALESCE(revoke_reason, ?) WHERE id=? AND tenant=?",
                (now.isoformat(), principal.id, reason, grant_id, principal.tenant),
            )
            if changed.rowcount != 1:
                raise Refusal("unknown_grant")

    def authorize(
        self,
        *,
        tenant: str,
        intent: str,
        action: str,
        amount_minor: int,
        currency: str,
        release: str,
        now: datetime,
    ) -> Grant:
        with reading(self.path) as connection:
            active = connection.execute(
                "SELECT grant_id FROM active_grant WHERE tenant=? AND intent=?", (tenant, intent)
            ).fetchone()
            if active is None:
                raise Refusal("not_active")
            row = self._usable(connection, tenant, active["grant_id"], now)
        if row["release"] != release:
            raise Refusal("release_changed")
        actions = json.loads(row["actions"])
        if action not in actions or row["currency"] != currency or amount_minor > row["max_amount_minor"]:
            raise Refusal("limits")
        return Grant(
            id=row["id"], tenant=row["tenant"], intent=row["intent"], tier=row["tier"], actions=actions,
            max_amount_minor=row["max_amount_minor"], currency=row["currency"], expires_at=row["expires_at"],
            release=row["release"],
        )

    def _usable(self, connection, tenant: str, grant_id: str, now: datetime):
        row = connection.execute(
            "SELECT * FROM grant_record WHERE id=? AND tenant=?", (grant_id, tenant)
        ).fetchone()
        if row is None:
            raise Refusal("unknown_grant")
        if row["revoked_at"] is not None:
            raise Refusal("revoked")
        if datetime.fromisoformat(row["expires_at"]) <= now:
            raise Refusal("expired")
        return row
