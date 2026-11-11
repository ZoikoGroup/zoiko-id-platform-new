"""Constrained retrieval over approved P02 spans (ADR-04). Lexical BM25, in memory, built from the signed snapshot.

Only spans of sources that pass `source_eligible` are ever returned, and eligibility is re-checked at query time so a withdrawal
takes effect immediately even though the index is built once (ADR-11). Retrieved text is DATA: it is never allowed to carry
instructions into the model (the gateway wraps it as quoted evidence).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from zid_assistant.domain.eligibility import Context, Snapshot, source_eligible

_WORD = re.compile(r"[a-z0-9][a-z0-9+-]*")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "this",
        "to",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
        "zoiko",
        "id",
        "use",
        "used",
        "using",
        "today",
        "work",
        "works",
        "tell",
        "please",
        "explain",
        "mean",
        "means",
        "about",
        "any",
        "have",
        "has",
        "get",
        "gets",
        "support",
        "supported",
        "supports",
    ]
)


def _toks(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOP]


@dataclass(frozen=True, slots=True)
class Hit:
    source_id: str
    span_id: str
    text: str
    score: float


class SpanIndex:
    def __init__(self, snapshot: Snapshot) -> None:
        self._docs: list[tuple[str, str, str, Counter[str], int]] = []
        df: Counter[str] = Counter()
        for src in snapshot.sources.values():
            for span in src.spans:
                toks = _toks(span.text + " " + src.title)
                c = Counter(toks)
                self._docs.append((src.source_id, span.span_id, span.text, c, len(toks)))
                df.update(c.keys())
        n = max(len(self._docs), 1)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self._avg = sum(d[4] for d in self._docs) / n if self._docs else 1.0
        self._max_idf = max(self._idf.values(), default=1.0)

    def search(
        self,
        snapshot: Snapshot,
        ctx: Context,
        query: str,
        k: int = 4,
        min_score: float = 1.6,
        min_coverage: float = 0.5,
    ) -> list[Hit]:
        q = _toks(query)
        if not q:
            return []
        weights = {
            t: self._idf.get(t, self._max_idf) for t in set(q)
        }  # a term the corpus lacks counts as rare, so it lowers coverage
        total = sum(weights.values()) or 1.0
        hits: list[Hit] = []
        for sid, span_id, text, tf, dl in self._docs:
            if not source_eligible(snapshot, sid, ctx).allowed:
                continue
            score = 0.0
            for term in q:
                f = tf.get(term, 0)
                if f:
                    score += (
                        self._idf.get(term, 0.0)
                        * (f * 2.2)
                        / (f + 1.2 * (0.25 + 0.75 * dl / self._avg))
                    )
            covered = sum(w for t, w in weights.items() if t in tf) / total
            if score >= min_score and covered >= min_coverage:
                hits.append(Hit(sid, span_id, text, score))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]
