"""Global search matching and ranking (ZID-UX-001 §3.1, §25). Pure domain, no I/O.

Search runs only over objects the caller may read, inside the caller's own tenant transaction, and
returns at most a few hits per type: it never reports counts or names of anything the caller cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_QUERY = 2
MAX_QUERY = 80
PER_TYPE = 5


@dataclass(frozen=True, slots=True)
class Hit:
    type: str
    id: str
    label: str
    hint: str
    href: str


def clean(query: str) -> str:
    """Trim and cap a query; control characters are dropped."""
    return "".join(ch for ch in query if ch.isprintable()).strip()[:MAX_QUERY]


def score(query: str, label: str) -> int | None:
    """None = no match. Lower is better: exact, then prefix, then word prefix, then substring."""
    q, text = query.casefold(), label.casefold()
    if text == q:
        return 0
    if text.startswith(q):
        return 1
    if any(
        word.startswith(q)
        for word in text.replace("/", " ").replace(".", " ").replace("-", " ").split()
    ):
        return 2
    if q in text:
        return 3
    return None


def rank(
    query: str, rows: list[tuple[str, str, str]], *, type: str, href: str, limit: int = PER_TYPE
) -> list[Hit]:
    scored = []
    for ident, label, hint in rows:
        s = score(query, label)
        if s is not None:
            scored.append((s, label.casefold(), Hit(type, ident, label, hint, href)))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [h for _, _, h in scored[:limit]]
