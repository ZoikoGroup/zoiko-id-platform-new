"""Deterministic intent routing (ZI-CHAT-KB-002 section 04). No model is involved in choosing the route (ADR-04).

Two layers: (1) built-in SAFETY rules that live in code, so a content mistake can never switch them off (account actions,
identity proofing, credential handling, human requests); (2) the 18 content-defined intents scored on phrases and keywords.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zid_assistant.application.snapshot import Knowledge
from zid_assistant.domain.content import Answer, Intent
from zid_assistant.domain.sensitive import normalize
from zid_assistant.domain.verdicts import Reason

_WORD = re.compile(r"[a-z0-9][a-z0-9'+-]*")

# Built-in safety rules: (name, compiled pattern, reason, default copy key). These never depend on content files.
_SAFETY: tuple[tuple[str, re.Pattern[str], Reason, str], ...] = (
    (
        "ACCOUNT_ACTION",
        re.compile(
            r"\b(?:reset|recover|unlock|restore|disable|delete|close|change|update|regain|get back)\b[^.?!]{0,30}\b(?:my|our)\b[^.?!]{0,20}\b(?:password|passkey|account|login|log-in|sign-in|2fa|mfa|authenticator|access|email address)\b"
            r"|\b(?:send|resend|give|text|email)\b[^.?!]{0,20}\b(?:me|us)\b[^.?!]{0,20}\b(?:a |the )?(?:code|otp|link|token)\b"
            r"|\bi (?:lost|can'?t|cannot|am locked out|forgot)\b[^.?!]{0,30}\b(?:access|log ?in|sign ?in|password|passkey|phone|device)\b"
            r"|\b(?:am i|is my|check my|show my|view my|what is my)\b[^.?!]{0,25}\b(?:verified|verification|account|status|balance|consent|session|credential)\b",
            re.IGNORECASE,
        ),
        Reason.ACCOUNT_ACTION,
        "refuse_account_access",
    ),
    (
        "IDENTITY_PROOFING",
        re.compile(
            r"\b(?:verify|prove|confirm|validate|check)\b[^.?!]{0,25}\b(?:my|our)\b[^.?!]{0,20}\b(?:identity|id\b|age|passport|licen[cs]e|face|selfie|documents?)\b"
            r"|\b(?:upload|send|attach|share|scan)\b[^.?!]{0,25}\b(?:id|passport|licen[cs]e|selfie|photo|document|biometric|face|fingerprint)\b",
            re.IGNORECASE,
        ),
        Reason.SECURE_ROUTE_ONLY,
        "refuse_identity_proofing",
    ),
    (
        "CREDENTIAL_HANDLING",
        re.compile(
            r"\b(?:here is|here's|this is|use|take)\b[^.?!]{0,15}\bmy\b[^.?!]{0,15}\b(?:password|api key|token|secret|private key|recovery code|passkey)\b"
            r"|\b(?:store|save|remember|keep)\b[^.?!]{0,20}\bmy\b[^.?!]{0,15}\b(?:password|key|token|secret|code)\b",
            re.IGNORECASE,
        ),
        Reason.SENSITIVE_INPUT,
        "refuse_credentials",
    ),
)
_HUMAN = re.compile(
    r"\b(?:talk|speak|chat)\b[^.?!]{0,20}\b(?:to|with)\b[^.?!]{0,15}\b(?:a |an |some )?(?:human|person|agent|someone|representative|sales|support|expert)\b"
    r"|\b(?:contact|reach|call|email)\b[^.?!]{0,15}\b(?:sales|support|your team|a human|someone)\b"
    r"|\b(?:book|request|schedule)\b[^.?!]{0,15}\b(?:a )?(?:demo|call|meeting)\b|\bget a quote\b|\bpricing for my\b",
    re.IGNORECASE,
)
_SUPPORT = re.compile(
    r"\b(?:support|help desk|helpdesk|ticket|report (?:a )?(?:bug|problem|issue)|something is broken|not working)\b",
    re.IGNORECASE,
)
_SALES = re.compile(
    r"\b(?:demo|sales|quote|procurement|pilot|partnership|enterprise plan|buy|purchase|contract)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RouteDecision:
    kind: str  # SAFETY | INTENT | HUMAN | NONE
    intent: Intent | None = None
    reason: Reason = Reason.OK
    copy_key: str | None = None
    purpose: str | None = None  # SALES | SUPPORT for HUMAN
    score: float = 0.0


_GREETING = re.compile(
    r"^(?:hi+|hello+|hey+|hiya|howdy|yo|greetings|good (?:morning|afternoon|evening)|namaste|hola|hi there|hello there|hey there)(?: (?:there|team|assistant|bot|zoiko(?: id)?))?[\s!.,?]*$"
)
_THANKS = re.compile(
    r"^(?:thanks?|thank you|thx|ok(?:ay)?|great|cool|got it|bye|goodbye)(?: (?:so much|a lot|very much))?[\s!.,?]*$"
)


def smalltalk(text: str) -> str | None:
    """A whole-message greeting or thanks (never part of a longer question), so it gets a friendly reply, not an out-of-scope refusal."""
    t = normalize(text).lower().strip()
    if _GREETING.match(t):
        return "greeting"
    if _THANKS.match(t):
        return "thanks"
    return None


def tokens(text: str) -> list[str]:
    return _WORD.findall(normalize(text).lower())


def _score(intent: Intent, text: str, words: set[str]) -> float:
    s = 0.0
    for p in intent.phrases:
        if p and p in text:
            s += 3.0
    for k in intent.keywords:
        if k and (k in words if " " not in k else k in text):
            s += 1.0
    return s


def route(k: Knowledge, text: str) -> RouteDecision:
    t = normalize(text).lower()
    for name, pattern, reason, copy_key in _SAFETY:
        if pattern.search(t):
            return RouteDecision("SAFETY", reason=reason, copy_key=copy_key)
    if _HUMAN.search(t):
        purpose = "SUPPORT" if (_SUPPORT.search(t) and not _SALES.search(t)) else "SALES"
        return RouteDecision(
            "HUMAN",
            reason=Reason.HUMAN_REQUESTED,
            purpose=purpose,
            copy_key=f"escalate_{purpose.lower()}",
        )
    words = set(tokens(text))
    best: tuple[float, int, Intent] | None = None
    for intent in k.intents.values():
        sc = _score(intent, t, words)
        if sc <= 0:
            continue
        cand = (sc, intent.priority, intent)
        if best is None or (cand[0], cand[1]) > (best[0], best[1]):
            best = cand
    if best is None:
        return RouteDecision("NONE", reason=Reason.OUT_OF_SCOPE)
    return RouteDecision("INTENT", intent=best[2], score=best[0])


def best_answer(
    k: Knowledge, text: str, intent: Intent | None, min_score: float = 1.0
) -> tuple[Answer, float] | None:
    """The approved answer entry that best matches the question, or None. Deterministic; threshold-guarded."""
    t = normalize(text).lower()
    words = set(tokens(text))
    best: tuple[float, Answer] | None = None
    for a in k.answers.values():
        if intent is not None and a.intent_id != intent.intent_id:
            continue
        score = 0.0
        for m in a.match:
            if m and m.lower() in t:
                score += 3.0
        q = set(tokens(a.question))
        if q:
            score += 4.0 * len(q & words) / len(q | words)
        if score > 0 and (best is None or score > best[0]):
            best = (score, a)
    if best is None or best[0] < min_score:
        return None
    return best[1], best[0]
