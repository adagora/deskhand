from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from casework.store import reading, transaction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_item (
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, case_id TEXT NOT NULL, operation_id TEXT, code TEXT NOT NULL,
  owner TEXT NOT NULL, opened_at TEXT NOT NULL, status TEXT NOT NULL, closed_by TEXT, closed_at TEXT, closed_how TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_item ON staff_item (tenant, case_id, code) WHERE status = 'open';
"""


@dataclass(frozen=True)
class StaffItem:
    id: str
    tenant: str
    case_id: str
    operation_id: str | None
    code: str
    owner: str
    opened_at: str
    status: str
    closed_by: str | None = None
    closed_at: str | None = None
    closed_how: str | None = None


class StaffQueue:
    def __init__(self, path: Path):
        self.path = Path(path)
        with transaction(self.path) as connection:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)

    def open(self, tenant: str, case_id: str, operation_id: str | None, code: str, owner: str, at: str) -> StaffItem:
        with transaction(self.path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO staff_item (id, tenant, case_id, operation_id, code, owner, opened_at, status)"
                " VALUES (?,?,?,?,?,?,?, 'open')",
                (str(uuid4()), tenant, case_id, operation_id, code, owner, at),
            )
            row = connection.execute(
                "SELECT * FROM staff_item WHERE tenant=? AND case_id=? AND code=? AND status='open'",
                (tenant, case_id, code),
            ).fetchone()
        return StaffItem(**dict(row))

    def close(self, tenant: str, case_id: str, actor: str, how: str, at: str) -> None:
        with transaction(self.path) as connection:
            connection.execute(
                "UPDATE staff_item SET status='closed', closed_by=?, closed_at=?, closed_how=?"
                " WHERE tenant=? AND case_id=? AND status='open'",
                (actor, at, how, tenant, case_id),
            )

    def items(self, tenant: str, status: str | None = None) -> list[StaffItem]:
        with reading(self.path) as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM staff_item WHERE tenant=? ORDER BY opened_at", (tenant,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM staff_item WHERE tenant=? AND status=? ORDER BY opened_at", (tenant, status)
                ).fetchall()
        return [StaffItem(**dict(row)) for row in rows]
