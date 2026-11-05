import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

CANCEL_REFUND = "cancel-refund"
_NAMED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Refusal(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def named(value) -> bool:
    return isinstance(value, str) and _NAMED.match(value) is not None


def evidence_digest(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class CustomerScope:
    tenant: str
    customer: str


@dataclass(frozen=True)
class Principal:
    tenant: str
    id: str
    roles: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Proposal:
    id: str
    tenant: str
    customer: str
    case_id: str
    booking_id: str
    booking_version: int
    intent: str
    action: str
    amount_minor: int
    currency: str
    payment_kind: str
    release: str
    review_required: bool
    business_at: str
    created_at: str
    expires_at: str

    def digest(self) -> str:
        return evidence_digest(asdict(self))


@dataclass(frozen=True)
class Consent:
    customer: str
    proposal_digest: str
    at: str


@dataclass(frozen=True)
class Review:
    reviewer: str
    verdict: str
    proposal_digest: str
    at: str


@dataclass(frozen=True)
class Resolution:
    tenant: str
    actor: str
    roles: list[str]
    kind: str
    at: str


@dataclass(frozen=True)
class Observation:
    state: str
    code: str | None = None
    booking_status: str | None = None
    booking_version: int | None = None
    amount_minor: int | None = None
    currency: str | None = None
    payment_kind: str | None = None


@dataclass(frozen=True)
class Timing:
    read_deadline_s: float = 900
    write_deadline_s: float = 259200
    attempt_timeout_s: float = 30
    first_retry_s: float = 1
    max_retry_s: float = 3600


@dataclass(frozen=True)
class CaseRequest:
    tenant: str
    customer: str
    case_id: str
    booking_id: str
    intent: str = "citywheels.cancellation"
    storm_claim: bool = False
    timing: Timing = field(default_factory=Timing)


@dataclass(frozen=True)
class Prepared:
    code: str | None = None
    proposal: Proposal | None = None
    settled: Observation | None = None


@dataclass(frozen=True)
class Gate:
    code: str | None = None
    grant_id: str | None = None


@dataclass(frozen=True)
class DecisionRef:
    id: str | None = None
    action: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class CaseResult:
    status: str
    code: str | None = None
    owner: str | None = None
    operation_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    payment_kind: str | None = None
    claimed_after_readback: bool = False
    resolved_by: str | None = None
    evidence: str = "not-recorded"


def consent_binds(consent: Consent | None, proposal: Proposal | None) -> bool:
    return bool(
        consent
        and proposal
        and consent.customer == proposal.customer
        and consent.proposal_digest == proposal.digest()
    )


def review_binds(review: Review | None, proposal: Proposal | None) -> bool:
    return bool(
        review
        and proposal
        and named(review.reviewer)
        and review.reviewer != proposal.customer
        and review.verdict in ("approved", "rejected")
        and review.proposal_digest == proposal.digest()
    )


def is_authorized(proposal: Proposal, consent: Consent | None, review: Review | None) -> bool:
    if not consent_binds(consent, proposal):
        return False
    if review_binds(review, proposal) and review.verdict == "rejected":
        return False
    if not proposal.review_required:
        return True
    return review_binds(review, proposal) and review.verdict == "approved"


def is_rejected(proposal: Proposal, review: Review | None) -> bool:
    return review_binds(review, proposal) and review.verdict == "rejected"
