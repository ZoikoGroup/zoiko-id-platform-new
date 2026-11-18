"""The evidence a printed-document authenticity check must earn before it may be switched on (ZID-DTV-WF-03 T24, T25; CR-125). Pure: labelled results in, a verdict per document type and check out.

Nothing here detects anything. `idv_image.PRODUCED` still lists the only checks the platform produces, and none of them says a document is genuine. A template, security-feature or photo-substitution check needs GENUINE specimens
of each document type and ATTACK samples made against it, run through the check, with the results counted. This module is the rule for what those counts must show. Its policy ships with none, so the gate never passes until the
security and QA owners record one, and the numbers are theirs: the right miss rate is a risk decision, not an engineering default.

Rules, in the direction of caution:
- a document type and check are judged together: a check that works on one document says nothing about another;
- only SPECIMEN and LIVE samples count. A SYNTHETIC sample (one we made ourselves) never counts as evidence of what a real forger does;
- an attack the check did not clearly FAIL is an attack it accepted, so an INCONCLUSIVE verdict on an attack counts as accepted;
- a genuine document the check FAILED is a bona fide rejection; one it could not judge (INCONCLUSIVE) is counted separately and also limits the gate, because a check that cannot judge most real documents is of little use;
- every attack kind the policy names must have been tried, on at least the required number of device models, so a check proved only against one kind of forgery is not proved against another.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

CHECKS = frozenset({"TEMPLATE", "TAMPER", "PHOTO_SUBSTITUTION"})
ATTACK_KINDS = frozenset(
    {"PRINT_COPY", "SCREEN_REPLAY", "TEXT_EDIT", "PHOTO_SUBSTITUTION", "TEMPLATE_FORGERY"}
)
COUNTED_SOURCES = frozenset({"SPECIMEN", "LIVE"})
VERDICTS = frozenset({"PASS", "FAIL", "INCONCLUSIVE"})


class EvaluationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Sample:
    document_type: str  # for example GB-PASSPORT-2019
    check: str
    genuine: bool
    attack_kind: str | None  # None for a genuine document
    source: str  # SPECIMEN | LIVE | SYNTHETIC
    device_model: str
    verdict: str

    def __post_init__(self) -> None:
        if self.check not in CHECKS or self.verdict not in VERDICTS:
            raise EvaluationError("SAMPLE_INVALID", f"{self.check}/{self.verdict}")
        if self.genuine == (self.attack_kind is not None) or (
            self.attack_kind is not None and self.attack_kind not in ATTACK_KINDS
        ):
            raise EvaluationError(
                "SAMPLE_INVALID",
                "a genuine sample has no attack kind and an attack names one of the known kinds",
            )
        if (
            self.source not in COUNTED_SOURCES | {"SYNTHETIC"}
            or not self.document_type.strip()
            or not self.device_model.strip()
        ):
            raise EvaluationError("SAMPLE_INVALID", "name the source, document type and device")


@dataclass(frozen=True, slots=True)
class Policy:
    min_genuine: int
    min_attacks: int
    max_attack_accept_rate: (
        float  # of the counted attacks, the share the check did not clearly fail
    )
    max_genuine_reject_rate: float  # of the counted genuine documents, the share it failed
    max_genuine_inconclusive_rate: (
        float  # of the counted genuine documents, the share it could not judge
    )
    required_attack_kinds: frozenset[str]
    min_devices: int


class Status(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    FAIL = "FAIL"
    PASS = "PASS"


@dataclass(frozen=True, slots=True)
class Result:
    document_type: str
    check: str
    status: Status
    reasons: tuple[str, ...]
    counts: Mapping[str, float]


def evaluate(
    samples: Iterable[Sample], policy: Policy | None, *, wanted: Iterable[tuple[str, str]] = ()
) -> list[Result]:
    """One result per document type and check seen (and per pair in `wanted`, so a check nobody tested is reported as untested, never silently absent)."""
    if policy is None:
        pairs = {(s.document_type, s.check) for s in samples} | set(wanted)
        return [
            Result(d, c, Status.NOT_CONFIGURED, ("NO_GATE_POLICY_RECORDED",), {})
            for d, c in sorted(pairs)
        ]
    groups: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for s in samples:
        groups[(s.document_type, s.check)].append(s)
    for pair in wanted:
        groups.setdefault(pair, [])
    out: list[Result] = []
    for (doc, check), items in sorted(groups.items()):
        counted = [s for s in items if s.source in COUNTED_SOURCES]
        genuine = [s for s in counted if s.genuine]
        attacks = [s for s in counted if not s.genuine]
        accepted = [s for s in attacks if s.verdict != "FAIL"]
        rejected = [s for s in genuine if s.verdict == "FAIL"]
        unsure = [s for s in genuine if s.verdict == "INCONCLUSIVE"]
        reasons: list[str] = []
        if len(genuine) < policy.min_genuine:
            reasons.append("TOO_FEW_GENUINE_DOCUMENTS")
        if len(attacks) < policy.min_attacks:
            reasons.append("TOO_FEW_ATTACKS")
        for kind in sorted(policy.required_attack_kinds - {s.attack_kind for s in attacks}):
            reasons.append(f"ATTACK_KIND_NOT_TRIED:{kind}")
        if len({s.device_model for s in counted}) < policy.min_devices:
            reasons.append("TOO_FEW_DEVICE_MODELS")
        accept_rate = len(accepted) / len(attacks) if attacks else 1.0
        reject_rate = len(rejected) / len(genuine) if genuine else 1.0
        unsure_rate = len(unsure) / len(genuine) if genuine else 1.0
        if attacks and accept_rate > policy.max_attack_accept_rate:
            reasons.append("ATTACKS_ACCEPTED_ABOVE_LIMIT")
        if genuine and reject_rate > policy.max_genuine_reject_rate:
            reasons.append("GENUINE_REJECTED_ABOVE_LIMIT")
        if genuine and unsure_rate > policy.max_genuine_inconclusive_rate:
            reasons.append("GENUINE_INCONCLUSIVE_ABOVE_LIMIT")
        ignored = len(items) - len(counted)
        out.append(
            Result(
                doc,
                check,
                Status.FAIL if reasons else Status.PASS,
                tuple(reasons),
                {
                    "genuine": len(genuine),
                    "attacks": len(attacks),
                    "attack_accept_rate": round(accept_rate, 4),
                    "genuine_reject_rate": round(reject_rate, 4),
                    "genuine_inconclusive_rate": round(unsure_rate, 4),
                    "synthetic_ignored": ignored,
                },
            )
        )
    return out


def may_switch_on(results: Iterable[Result], document_type: str, check: str) -> bool:
    """A check is allowed for a document type only when that very pair passed."""
    return any(
        r.document_type == document_type and r.check == check and r.status is Status.PASS
        for r in results
    )


_RESULT_KEYS = {
    "document_type",
    "check",
    "genuine",
    "attack_kind",
    "source",
    "device_model",
    "verdict",
}


def parse_samples(doc: object) -> list[Sample]:
    """A results file: a list of samples. An unknown key or a value this module does not know refuses the whole file."""
    if not isinstance(doc, list):
        raise EvaluationError("RESULTS_INVALID", "a list of samples")
    out: list[Sample] = []
    for item in doc:
        if (
            not isinstance(item, dict)
            or set(item) - _RESULT_KEYS
            or not {"document_type", "check", "genuine", "source", "device_model", "verdict"}
            <= set(item)
        ):
            raise EvaluationError("RESULTS_INVALID", "unknown or missing keys")
        if not isinstance(item["genuine"], bool):
            raise EvaluationError("RESULTS_INVALID", "genuine is true or false")
        out.append(
            Sample(
                str(item["document_type"]),
                str(item["check"]).upper(),
                item["genuine"],
                item.get("attack_kind") and str(item["attack_kind"]).upper(),
                str(item["source"]).upper(),
                str(item["device_model"]),
                str(item["verdict"]).upper(),
            )
        )
    return out
