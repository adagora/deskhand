import hashlib
import hmac
import json
from pathlib import Path

from casework.model import Principal, Refusal


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Directory:
    def __init__(self, entries: list[dict]):
        self._entries = entries

    @classmethod
    def from_file(cls, path: Path) -> "Directory":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def authenticate(self, token: str) -> Principal:
        if not isinstance(token, str) or not token:
            raise Refusal("unauthenticated")
        presented = token_digest(token)
        for entry in self._entries:
            if hmac.compare_digest(entry["sha256"], presented):
                return Principal(tenant=entry["tenant"], id=entry["id"], roles=list(entry["roles"]))
        raise Refusal("unauthenticated")
