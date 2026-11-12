"""Deterministic offline provider. The default until the vendor decision is signed (OD-010/016); also the test double."""

from __future__ import annotations

import json
import re

from .base import DraftRequest, DraftResult

_SENT = re.compile(r"(?<=[.!?])\s+")


class MockProvider:
    name = "mock"

    async def draft(self, request: DraftRequest) -> DraftResult:
        if not request.evidence:
            body = {"decision": "ABSTAIN", "answer_text": "", "span_refs": []}
        else:
            best = request.evidence[0]
            sentences = _SENT.split(best.text.strip())
            body = {
                "decision": "ANSWER",
                "answer_text": " ".join(sentences[:2]).strip(),
                "span_refs": [best.ref],
            }
        return DraftResult(raw_text=json.dumps(body), provider=self.name, model="mock-1")
