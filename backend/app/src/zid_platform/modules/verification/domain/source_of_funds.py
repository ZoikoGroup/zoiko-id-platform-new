"""Source of funds and wealth: questionnaires as data, and a case that a person decides (ZID-GAP-001 v2.0 DTV-28; CR-073 P2-6; CR-096). Pure domain.

This is the one regulated line that is mostly ours: questions, evidence and review, no licensed partner. The rules here are the ones that must hold
whatever questionnaire a compliance officer writes:

- a questionnaire is DATA (`config/questionnaires/...`), versioned, and identified by a content hash. A case records the hash it was answered against,
  so the questions a person saw can never change under them. A questionnaire still marked SAMPLE is for tests and demonstration: it may never be
  used on a line that is LIVE;
- a case holds ANSWERS and REFERENCES to evidence (an upload id and its type), never the evidence itself, and never a file name or free-text
  identifier of a document. Amounts are decimal strings with a currency, never floats;
- the system never clears anyone. The most it decides alone is that a submission is INCOMPLETE (what is missing, so the person can fix it) or
  complete and waiting for a person. CLEARED and NOT_CLEARED are only ever the decision of a human reviewer who is not the person who submitted
  the case, with reason codes from a fixed list. No free text is kept anywhere, so the decision record holds no personal data;
- an adverse outcome is never final on first decision: it is NOT_CLEARED_PENDING_APPEAL, and the person can appeal. An appeal is decided by a
  reviewer who is neither the submitter nor the first reviewer, and only then can an adverse outcome become final;
- a clearance expires (`CLEARANCE_VALID_DAYS`): wealth and funds change, and an old answer is not a current one;
- there is no score. Nothing here adds up risk, and nothing numeric decides an outcome.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

LINE = "source_of_funds"
CLEARANCE_VALID_DAYS = 365
MAX_TEXT = 1000
MAX_QUESTIONS = 60
MAX_EVIDENCE_PER_ANSWER = 8
# Evidence types a questionnaire may ask for. A type is a kind of document, never a file.
EVIDENCE_TYPES = frozenset(
    {
        "PAYSLIP", "TAX_RETURN", "BANK_STATEMENT", "EMPLOYMENT_CONTRACT", "COMPANY_ACCOUNTS", "DIVIDEND_VOUCHER", "SALE_CONTRACT",
        "PROPERTY_COMPLETION_STATEMENT", "PROBATE_OR_WILL", "INVESTMENT_STATEMENT", "GIFT_LETTER", "LOAN_AGREEMENT", "ACCOUNTANT_LETTER",
        "OTHER_DOCUMENT",
    }
)  # fmt: skip
REASON_CODES = frozenset(
    {
        "FUNDS_EXPLAINED_AND_EVIDENCED", "EVIDENCE_CONSISTENT_WITH_ANSWERS", "EVIDENCE_INSUFFICIENT", "EVIDENCE_INCONSISTENT",
        "SOURCE_NOT_EXPLAINED", "AMOUNT_NOT_SUPPORTED", "DOCUMENT_UNREADABLE", "OUT_OF_SCOPE", "APPEAL_NEW_EVIDENCE", "APPEAL_ERROR_FOUND",
        "APPEAL_NO_NEW_INFORMATION",
    }
)  # fmt: skip
ID = re.compile(r"^[A-Z][A-Z0-9-]{1,31}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


class SofError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Kind(StrEnum):
    TEXT = "TEXT"
    CHOICE = "CHOICE"
    AMOUNT = "AMOUNT"  # {"value": "1234.50", "currency": "GBP"}
    DATE = "DATE"
    YES_NO = "YES_NO"


class Status(StrEnum):
    SAMPLE = "SAMPLE"  # written by engineering to test the machinery; no compliance officer has approved it
    APPROVED = "APPROVED"  # approved by the named owner, with a legal basis reference


class CaseState(StrEnum):
    DRAFT = "DRAFT"
    INCOMPLETE = "INCOMPLETE"  # submitted but something is missing: the person is told what
    NEEDS_REVIEW = "NEEDS_REVIEW"  # complete and waiting for a person
    CLEARED = "CLEARED"
    NOT_CLEARED_PENDING_APPEAL = "NOT_CLEARED_PENDING_APPEAL"
    APPEALED = "APPEALED"
    NOT_CLEARED_FINAL = "NOT_CLEARED_FINAL"
    CANCELED = "CANCELED"


TERMINAL = frozenset({CaseState.CLEARED, CaseState.NOT_CLEARED_FINAL, CaseState.CANCELED})


class Decision(StrEnum):
    CLEAR = "CLEAR"
    NOT_CLEAR = "NOT_CLEAR"
    ASK_FOR_MORE = "ASK_FOR_MORE"  # back to the person, with what is needed; not an adverse outcome


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    text: str
    kind: Kind
    required: bool = True
    choices: tuple[str, ...] = ()
    # answer value -> evidence types the case must hold a reference to when that answer is given ("*" applies to any answer)
    evidence_by_answer: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Questionnaire:
    code: str
    version: str
    status: Status
    countries: tuple[str, ...]
    questions: tuple[Question, ...]
    approved_by: str | None
    legal_basis_ref: str | None
    content_hash: str

    def question(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.question_id == question_id), None)

    @property
    def usable_when_live(self) -> bool:
        return self.status is Status.APPROVED and bool(self.approved_by and self.legal_basis_ref)


def _canonical(doc: Mapping[str, Any]) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def parse(doc: Mapping[str, Any]) -> Questionnaire:
    """Build a questionnaire from configuration. Anything malformed refuses the whole file: a half-read questionnaire would ask the wrong questions."""
    try:
        code, version = str(doc["code"]), str(doc["version"])
        status = Status(str(doc.get("status", "SAMPLE")))
    except (KeyError, ValueError) as e:
        raise SofError("QUESTIONNAIRE_INVALID", f"code, version or status: {e}") from e
    if code != LINE:
        raise SofError("QUESTIONNAIRE_INVALID", f"code must be {LINE}")
    if not version:
        raise SofError("QUESTIONNAIRE_INVALID", "version")
    countries = tuple(sorted({str(c).upper() for c in doc.get("countries") or ()}))
    if not countries or any(not re.fullmatch(r"[A-Z]{2}", c) for c in countries):
        raise SofError("QUESTIONNAIRE_INVALID", "countries must be ISO codes and not empty")
    approved_by = str(doc["approved_by"]) if doc.get("approved_by") else None
    legal = str(doc["legal_basis_ref"]) if doc.get("legal_basis_ref") else None
    if status is Status.APPROVED and not (approved_by and legal):
        raise SofError(
            "QUESTIONNAIRE_APPROVAL_INCOMPLETE", "APPROVED needs approved_by and legal_basis_ref"
        )
    raw = doc.get("questions") or []
    if not raw or len(raw) > MAX_QUESTIONS:
        raise SofError("QUESTIONNAIRE_INVALID", f"1 to {MAX_QUESTIONS} questions")
    questions: list[Question] = []
    seen: set[str] = set()
    for item in raw:
        qid = str(item.get("id", ""))
        if not ID.match(qid) or qid in seen:
            raise SofError("QUESTIONNAIRE_INVALID", f"question id {qid!r} is malformed or repeated")
        seen.add(qid)
        try:
            kind = Kind(str(item.get("answer")))
        except ValueError as e:
            raise SofError("QUESTIONNAIRE_INVALID", f"{qid}: answer type") from e
        text = str(item.get("text", "")).strip()
        if not text or len(text) > MAX_TEXT:
            raise SofError("QUESTIONNAIRE_INVALID", f"{qid}: text")
        choices = tuple(str(c) for c in item.get("choices") or ())
        if (kind is Kind.CHOICE) != bool(choices):
            raise SofError(
                "QUESTIONNAIRE_INVALID",
                f"{qid}: choices belong to CHOICE questions, and CHOICE needs them",
            )
        evidence: dict[str, tuple[str, ...]] = {}
        for answer, types in (item.get("evidence_required_for") or {}).items():
            wanted = tuple(str(t) for t in types)
            if not wanted or len(wanted) > MAX_EVIDENCE_PER_ANSWER or set(wanted) - EVIDENCE_TYPES:
                raise SofError("QUESTIONNAIRE_INVALID", f"{qid}: evidence types for {answer}")
            if str(answer) != "*" and (kind is not Kind.CHOICE or str(answer) not in choices):
                raise SofError(
                    "QUESTIONNAIRE_INVALID", f"{qid}: evidence is keyed to a choice or to '*'"
                )
            evidence[str(answer)] = wanted
        questions.append(
            Question(qid, text, kind, bool(item.get("required", True)), choices, evidence)
        )
    return Questionnaire(
        code,
        version,
        status,
        countries,
        tuple(questions),
        approved_by,
        legal,
        hashlib.sha256(_canonical(doc)).hexdigest(),
    )


# ----------------------------------------------------------------------------------------------------------------------- answers


def _valid_answer(q: Question, value: Any) -> bool:
    if q.kind is Kind.TEXT:
        return isinstance(value, str) and 0 < len(value.strip()) <= MAX_TEXT
    if q.kind is Kind.CHOICE:
        return isinstance(value, str) and value in q.choices
    if q.kind is Kind.YES_NO:
        return isinstance(value, bool)
    if q.kind is Kind.DATE:
        try:
            return isinstance(value, str) and date.fromisoformat(value) <= date.today()
        except ValueError:
            return False
    if q.kind is Kind.AMOUNT:
        if not (isinstance(value, Mapping) and set(value) == {"value", "currency"}):
            return False
        try:
            amount = Decimal(str(value["value"]))
        except InvalidOperation:
            return False
        return (
            isinstance(value["value"], str)
            and amount.is_finite()
            and amount >= 0
            and bool(CURRENCY.match(str(value["currency"])))
        )
    return False


@dataclass(frozen=True, slots=True)
class Assessment:
    complete: bool
    problems: tuple[str, ...]  # codes, each naming the question: ANSWER_MISSING:SOF-01
    needed_evidence: tuple[
        tuple[str, str], ...
    ]  # (question id, evidence type) the case must still reference


def assess(
    q: Questionnaire, answers: Mapping[str, Any], evidence: Sequence[Mapping[str, str]]
) -> Assessment:
    """What is missing or wrong. Never a verdict: complete only means a person can now look at it.

    `evidence` is a list of {"question_id", "type", "upload_id"} references."""
    problems: list[str] = []
    needed: list[tuple[str, str]] = []
    for qid in answers:
        if q.question(qid) is None:
            problems.append(f"ANSWER_UNKNOWN_QUESTION:{qid}")
    held = {(e.get("question_id", ""), e.get("type", "")) for e in evidence if e.get("upload_id")}
    for e in evidence:
        if q.question(e.get("question_id", "")) is None or e.get("type") not in EVIDENCE_TYPES:
            problems.append(f"EVIDENCE_REFERENCE_INVALID:{e.get('question_id', '')}")
    for question in q.questions:
        if question.question_id not in answers:
            if question.required:
                problems.append(f"ANSWER_MISSING:{question.question_id}")
            continue
        value = answers[question.question_id]
        if not _valid_answer(question, value):
            problems.append(f"ANSWER_INVALID:{question.question_id}")
            continue
        wanted = set(question.evidence_by_answer.get("*", ()))
        if isinstance(value, str):
            wanted |= set(question.evidence_by_answer.get(value, ()))
        for kind in sorted(wanted):
            if (question.question_id, kind) not in held:
                problems.append(f"EVIDENCE_MISSING:{question.question_id}:{kind}")
                needed.append((question.question_id, kind))
    return Assessment(not problems, tuple(dict.fromkeys(problems)), tuple(needed))


def questionnaire_for_case(q: Questionnaire, country: str, *, line_live: bool) -> Questionnaire:
    """The questionnaire a case may use in this country, or an error. A SAMPLE one is refused when the line is LIVE."""
    if country.upper() not in q.countries:
        raise SofError("QUESTIONNAIRE_NOT_FOR_COUNTRY", country.upper())
    if line_live and not q.usable_when_live:
        raise SofError("QUESTIONNAIRE_NOT_APPROVED", f"{q.code} {q.version} is {q.status.value}")
    return q


# --------------------------------------------------------------------------------------------------------------------- the case


@dataclass(frozen=True, slots=True)
class Outcome:
    state: CaseState
    decision: Decision | None = None
    valid_until: datetime | None = None
    reason_codes: tuple[str, ...] = ()


def after_submission(a: Assessment) -> CaseState:
    """The only thing the system decides alone: can a person look at this yet."""
    return CaseState.NEEDS_REVIEW if a.complete else CaseState.INCOMPLETE


def decide(
    state: CaseState,
    decision: Decision,
    *,
    reviewer: str,
    submitted_by: str,
    first_reviewer: str | None,
    reasons: Sequence[str],
    now: datetime,
) -> Outcome:
    """A human decision. Refused unless the case is waiting for one, the reviewer is independent, and the reasons are from the fixed list."""
    if state not in {CaseState.NEEDS_REVIEW, CaseState.APPEALED}:
        raise SofError("CASE_NOT_AWAITING_DECISION", state.value)
    if not reviewer or reviewer == submitted_by:
        raise SofError(
            "REVIEWER_NOT_INDEPENDENT", "the person who submitted a case cannot decide it"
        )
    appeal = state is CaseState.APPEALED
    if appeal and reviewer == first_reviewer:
        raise SofError(
            "APPEAL_NEEDS_A_DIFFERENT_REVIEWER",
            "an appeal is decided by someone who did not decide first",
        )
    codes = tuple(dict.fromkeys(reasons))
    if not codes or set(codes) - REASON_CODES:
        raise SofError("REASON_CODE_REQUIRED", "one or more codes from the fixed list")
    if decision is Decision.CLEAR:
        return Outcome(
            CaseState.CLEARED, decision, now + timedelta(days=CLEARANCE_VALID_DAYS), codes
        )
    if decision is Decision.NOT_CLEAR:
        final = CaseState.NOT_CLEARED_FINAL if appeal else CaseState.NOT_CLEARED_PENDING_APPEAL
        return Outcome(final, decision, None, codes)
    if appeal:
        raise SofError("APPEAL_CANNOT_ASK_FOR_MORE", "an appeal clears or does not clear")
    return Outcome(CaseState.INCOMPLETE, decision, None, codes)


def appeal(state: CaseState, *, by: str, subject: str, submitted_by: str) -> CaseState:
    """Only the person the case is about (or the one who submitted it for them) may appeal, and only an adverse first decision."""
    if state is not CaseState.NOT_CLEARED_PENDING_APPEAL:
        raise SofError("CASE_NOT_APPEALABLE", state.value)
    if by not in {subject, submitted_by}:
        raise SofError("APPEAL_NOT_BY_THE_PERSON", "only the person concerned may appeal")
    return CaseState.APPEALED


def is_clear(state: CaseState, valid_until: datetime | None, now: datetime) -> bool:
    """A clearance counts only while it has not lapsed."""
    return state is CaseState.CLEARED and valid_until is not None and now < valid_until
