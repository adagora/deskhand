"""Verbatim search over the CityWheels policy markdown."""

import re
from pathlib import Path

from citywheels.paths import default_data_dir

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_HEADING = re.compile(r"(?m)^#{1,3} .+$")

STOP = {
    "a", "an", "the", "is", "of", "for", "my", "do", "you", "i", "to", "on",
    "and", "or", "if", "me", "we", "can", "much", "what", "how", "with", "get",
    "back", "want", "please", "does", "it", "be", "at", "in", "from", "your",
    "our", "am", "have", "has", "will", "would", "should", "about", "this",
    "that", "there", "whole", "some", "any", "are", "when", "who", "which",
    "did", "was", "were", "been", "being", "into", "over", "under", "than",
    "then", "also", "just", "not", "no", "yes", "happens",
}

SYNONYMS = {
    "e-bike": ["electric", "ebike"],
    "ebike": ["e-bike", "electric"],
    "electric": ["e-bike", "ebike"],
    "saturday": ["sunday"],
    "sunday": ["saturday"],
    "open": ["opening", "hours", "closed"],
    "opening": ["open", "hours"],
    "hours": ["open", "opening"],
    "cancel": ["cancellation", "refund", "cancelled"],
    "cancellation": ["cancel", "refund"],
    "refund": ["refunded", "cancellation", "cancel"],
    "kids": ["children", "child"],
    "child": ["children", "kids"],
    "kid": ["kids", "children", "child"],
    "rent": ["rental", "rented"],
    "late": ["delay"],
    "stolen": ["lost"],
    "lost": ["stolen"],
    "pay": ["payment", "paid"],
    "payment": ["pay", "paid"],
    "dog": ["dogs"],
    "age": ["years", "old"],
    "year": ["years", "old", "age"],
    "old": ["years", "age"],
    "where": ["entrance", "park", "address"],
    "helmet": ["helmets"],
    "voucher": ["vouchers"],
    "price": ["eur"],
    "cost": ["price", "eur"],
    "train": ["abroad"],
    "abroad": ["country"],
    "weather": ["storm"],
    "storm": ["weather"],
    "break": ["accident"],
    "accident": ["break"],
    "fee": ["fees"],
    "change": ["changed", "changing"],
    "booking": ["bookings"],
}


def tokenize(text: str) -> list[str]:
    found: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        parts = token.split("-")
        candidates = [token, *parts] if len(parts) > 1 else [token]
        for candidate in candidates:
            if not candidate or candidate in STOP:
                continue
            if len(candidate) == 1 and not candidate.isdigit():
                continue
            found.append(candidate)
    return found


def _sections(text: str, source: str) -> list[dict]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        stripped = text.strip()
        if not stripped:
            return []
        return [{"source": source, "heading": source, "text": stripped}]
    sections = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if not chunk:
            continue
        heading = match.group(0).lstrip("#").strip()
        sections.append({"source": source, "heading": heading, "text": chunk})
    return sections


def _score(query_tokens: list[str], section: dict) -> int:
    text = section["text"].lower()
    heading = section["heading"].lower()
    score = 0
    seen: set[str] = set()
    for token in query_tokens:
        if token in seen:
            continue
        seen.add(token)
        forms = [token, *SYNONYMS.get(token, [])]
        if token in text or token in heading:
            score += 4
        for form in forms:
            if form in heading:
                score += 5
            elif form in text:
                score += 3
    return score


def search_policies(query: str, data_dir: Path | None = None, limit: int = 5) -> dict:
    """Return verbatim policy passages that overlap the query. Empty if nothing matches."""
    directory = data_dir or default_data_dir()
    cleaned = str(query or "").strip()
    if not cleaned:
        return {"query": "", "passages": []}
    tokens = tokenize(cleaned)
    sections: list[dict] = []
    policy_dir = directory / "policies"
    for path in sorted(policy_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sections.extend(_sections(text, path.name))
    ranked = []
    for index, section in enumerate(sections):
        ranked.append((_score(tokens, section), index, section))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    passages = [section for score, _, section in ranked if score > 0][:limit]
    return {"query": cleaned, "passages": passages}
