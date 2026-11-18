"""Independent cross-check of our own chip reader against a reference reader (ZID-DTV-WF-01 and WF-03 engineering item E03; CR-123). Pure: two runs in, a comparison out; many comparisons in, a gate verdict out.

The wireframes allow a self-run chip claim only "if independent self-run assurance gates pass", and treat JMRTD (Android) and NFCPassportReader (iOS) as research and evaluation tools, not as the product. This module is the
gate's rules. A lab runs the SAME physical document through our reader and through a reference reader on the same device; each run produces a report in the shape below, with the READ report (what was read) and the
CRYPTOGRAPHIC report (what was judged) kept apart, as E03 requires. Nothing here reads a chip, and neither reference library is linked into the product.

What a run holds is identifiers and hashes only: an opaque lab reference for the document (never its number), the SHA-256 of the signed object and of each data group, and the verdict of each check. No data group content, no name, no photo.

Rules:
- two runs are comparable only if they are for the same document reference and read the same signed object (same SOD hash); otherwise the pair is `DIFFERENT_DOCUMENT_OR_SOD` and counts for nothing;
- the READ report agrees only if the same data groups were read with identical hashes;
- each cryptographic check agrees, disagrees or is not comparable (either side did not run it). A PASS on one side and a FAIL on the other is a disagreement, and one where OUR reader passed what the reference failed is CRITICAL: it
  is the direction that would let a bad passport through;
- the gate never passes unless an owner has recorded a policy (it ships with none), needs enough distinct documents, devices, protocols and reference readers, and needs no critical disagreement at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

READERS = frozenset({"ZOIKO", "JMRTD", "NFCPASSPORTREADER"})
REFERENCES = frozenset({"JMRTD", "NFCPASSPORTREADER"})
ACCESS = frozenset({"BAC", "PACE_GM", "PACE_CAM", "PACE_IM"})
CHECKS = ("passive_auth", "issuer_chain", "revocation", "active_auth", "chip_auth")
_PASS = {"PASS", "TRUSTED", "CHECKED_GOOD"}
_FAIL = {"FAIL", "UNTRUSTED", "REVOKED"}
_NOT_RUN = {"NOT_RUN", "NOT_CHECKED", "NOT_SUPPORTED", "INDETERMINATE"}


class CrosscheckError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReadReport:
    sod_sha256: str
    groups: Mapping[int, str]  # data group number -> SHA-256 of its bytes


@dataclass(frozen=True, slots=True)
class CryptoReport:
    passive_auth: str = "NOT_RUN"  # PASS | FAIL | NOT_RUN | INDETERMINATE
    issuer_chain: str = "NOT_CHECKED"  # TRUSTED | UNTRUSTED | NOT_CHECKED
    revocation: str = "NOT_CHECKED"  # CHECKED_GOOD | REVOKED | NOT_CHECKED
    active_auth: str = "NOT_RUN"  # PASS | FAIL | NOT_SUPPORTED | NOT_RUN
    chip_auth: str = "NOT_RUN"  # PASS | FAIL | NOT_SUPPORTED | NOT_RUN


@dataclass(frozen=True, slots=True)
class ChipRun:
    reader: str  # ZOIKO | JMRTD | NFCPASSPORTREADER
    reader_version: str
    device_model: str
    os_version: str
    document_ref: str  # an opaque lab id for the physical document, never its number
    access: str  # BAC | PACE_GM | PACE_CAM | PACE_IM
    read: ReadReport
    crypto: CryptoReport = field(default_factory=CryptoReport)

    def __post_init__(self) -> None:
        if self.reader not in READERS:
            raise CrosscheckError("READER_UNKNOWN", self.reader)
        if self.access not in ACCESS:
            raise CrosscheckError("ACCESS_UNKNOWN", self.access)
        if not self.document_ref.strip() or not self.device_model.strip():
            raise CrosscheckError("RUN_INCOMPLETE", "name the document reference and the device")


class Agreement(StrEnum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class Severity(StrEnum):
    NONE = "NONE"
    REFERENCE_STRICTER = "REFERENCE_STRICTER"  # the reference failed what ours passed is CRITICAL, so this is the other way round: ours failed, the reference passed
    CRITICAL = "CRITICAL"  # ours passed what the reference failed


@dataclass(frozen=True, slots=True)
class Comparison:
    comparable: bool
    reason: str | None
    read: Agreement
    read_differences: tuple[str, ...]
    checks: Mapping[str, Agreement]
    critical: tuple[str, ...]  # checks where ours passed and the reference failed
    ours_stricter: tuple[str, ...]  # checks where ours failed and the reference passed
    access: str
    device_model: str
    document_ref: str
    reference: str


def _verdict(v: str) -> str:
    if v in _PASS:
        return "PASS"
    if v in _FAIL:
        return "FAIL"
    return "NONE"


def compare(ours: ChipRun, reference: ChipRun) -> Comparison:
    if ours.reader != "ZOIKO":
        raise CrosscheckError("OURS_MUST_BE_ZOIKO", ours.reader)
    if reference.reader not in REFERENCES:
        raise CrosscheckError("REFERENCE_MUST_BE_INDEPENDENT", reference.reader)

    def void(reason: str) -> Comparison:
        return Comparison(
            False,
            reason,
            Agreement.NOT_COMPARABLE,
            (),
            {},
            (),
            (),
            ours.access,
            ours.device_model,
            ours.document_ref,
            reference.reader,
        )

    if ours.document_ref != reference.document_ref:
        return void("DIFFERENT_DOCUMENT_OR_SOD")
    if ours.read.sod_sha256 != reference.read.sod_sha256:
        return void("DIFFERENT_DOCUMENT_OR_SOD")
    if ours.device_model != reference.device_model:
        return void(
            "DIFFERENT_DEVICE"
        )  # the same phone for both runs, or a difference could be the phone's

    diffs: list[str] = []
    for group in sorted(set(ours.read.groups) | set(reference.read.groups)):
        a, b = ours.read.groups.get(group), reference.read.groups.get(group)
        if a is None or b is None:
            diffs.append(f"DG{group}_READ_BY_ONE_ONLY")
        elif a != b:
            diffs.append(f"DG{group}_BYTES_DIFFER")
    checks: dict[str, Agreement] = {}
    critical: list[str] = []
    stricter: list[str] = []
    for name in CHECKS:
        a, b = _verdict(getattr(ours.crypto, name)), _verdict(getattr(reference.crypto, name))
        if a == "NONE" or b == "NONE":
            checks[name] = Agreement.NOT_COMPARABLE
        elif a == b:
            checks[name] = Agreement.AGREE
        else:
            checks[name] = Agreement.DISAGREE
            (critical if a == "PASS" else stricter).append(name)
    return Comparison(
        True, None, Agreement.DISAGREE if diffs else Agreement.AGREE, tuple(diffs), checks, tuple(critical), tuple(stricter),
        ours.access, ours.device_model, ours.document_ref, reference.reader,
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class GatePolicy:
    min_documents: int
    min_devices: int
    required_access: frozenset[str]
    min_runs_per_access: int
    required_references: frozenset[str]  # every one of these must have been used
    min_comparable_passive_auth_share: float = (
        1.0  # of the comparable pairs, the share where passive authentication was compared
    )


class Status(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    FAIL = "FAIL"
    PASS = "PASS"


@dataclass(frozen=True, slots=True)
class Gate:
    status: Status
    reasons: tuple[str, ...]
    counts: Mapping[str, int]


def gate(comparisons: Iterable[Comparison], policy: GatePolicy | None) -> Gate:
    """Pass only with a recorded policy and enough independent evidence, and never with a single critical disagreement."""
    if policy is None:
        return Gate(Status.NOT_CONFIGURED, ("NO_GATE_POLICY_RECORDED",), {})
    usable = [c for c in comparisons if c.comparable]
    reasons: list[str] = []
    documents = {c.document_ref for c in usable}
    devices = {c.device_model for c in usable}
    references = {c.reference for c in usable}
    per_access: dict[str, int] = {a: 0 for a in policy.required_access}
    for c in usable:
        if c.access in per_access:
            per_access[c.access] += 1
    if any(c.critical for c in usable):
        reasons.append("CRITICAL_DISAGREEMENT")  # ours passed what an independent reader failed
    if any(c.read is Agreement.DISAGREE for c in usable):
        reasons.append("READ_REPORTS_DISAGREE")
    if any(a is Agreement.DISAGREE for c in usable for a in c.checks.values()):
        reasons.append("CRYPTOGRAPHIC_REPORTS_DISAGREE")
    if len(documents) < policy.min_documents:
        reasons.append("TOO_FEW_DOCUMENTS")
    if len(devices) < policy.min_devices:
        reasons.append("TOO_FEW_DEVICE_MODELS")
    for access, n in sorted(per_access.items()):
        if n < policy.min_runs_per_access:
            reasons.append(f"TOO_FEW_RUNS:{access}")
    for ref in sorted(policy.required_references - references):
        reasons.append(f"REFERENCE_NOT_USED:{ref}")
    compared = sum(
        1 for c in usable if c.checks.get("passive_auth") is not Agreement.NOT_COMPARABLE
    )
    if usable and compared / len(usable) < policy.min_comparable_passive_auth_share:
        reasons.append("PASSIVE_AUTH_NOT_COMPARED_ENOUGH")
    if not usable:
        reasons.append("NO_COMPARABLE_RUNS")
    counts = {
        "comparable": len(usable),
        "documents": len(documents),
        "devices": len(devices),
        "references": len(references),
    }
    return Gate(Status.FAIL if reasons else Status.PASS, tuple(dict.fromkeys(reasons)), counts)


# ---- the run file a harness app emits (docs/qa/wf01-chip-crosscheck-plan.md, sdks/native-bridge/crosscheck)

_RUN_KEYS = {
    "reader",
    "reader_version",
    "device_model",
    "os_version",
    "document_ref",
    "access",
    "read",
    "crypto",
}
_HEX64 = frozenset("0123456789abcdef")


def _hash(value: object, what: str) -> str:
    text = str(value).lower()
    if len(text) != 64 or not set(text) <= _HEX64:
        raise CrosscheckError("HASH_INVALID", what)
    return text


def parse_run(doc: Mapping[str, object]) -> ChipRun:
    """Read one run file strictly: an unknown key, a hash that is not 64 hex characters, or a verdict word this module does not know refuses the whole file."""
    if not isinstance(doc, Mapping) or set(doc) - _RUN_KEYS:
        raise CrosscheckError("RUN_FILE_INVALID", "unknown or missing keys")
    read, crypto = doc.get("read"), doc.get("crypto")
    if (
        not isinstance(read, Mapping)
        or set(read) - {"sod_sha256", "groups"}
        or not isinstance(read.get("groups"), Mapping)
    ):
        raise CrosscheckError("RUN_FILE_INVALID", "read")
    groups: dict[int, str] = {}
    for key, value in read["groups"].items():
        if not str(key).isdigit() or not 1 <= int(key) <= 16:
            raise CrosscheckError("RUN_FILE_INVALID", f"data group {key}")
        groups[int(key)] = _hash(value, f"DG{key}")
    allowed = {
        "passive_auth": {"PASS", "FAIL", "NOT_RUN", "INDETERMINATE"},
        "issuer_chain": {"TRUSTED", "UNTRUSTED", "NOT_CHECKED"},
        "revocation": {"CHECKED_GOOD", "REVOKED", "NOT_CHECKED"},
        "active_auth": {"PASS", "FAIL", "NOT_SUPPORTED", "NOT_RUN"},
        "chip_auth": {"PASS", "FAIL", "NOT_SUPPORTED", "NOT_RUN"},
    }
    if not isinstance(crypto, Mapping) or set(crypto) - set(allowed):
        raise CrosscheckError("RUN_FILE_INVALID", "crypto")
    for name, value in crypto.items():
        if value not in allowed[name]:
            raise CrosscheckError("VERDICT_UNKNOWN", f"{name}={value}")
    try:
        return ChipRun(
            str(doc["reader"]).upper(), str(doc["reader_version"]), str(doc["device_model"]), str(doc["os_version"]), str(doc["document_ref"]), str(doc["access"]).upper(),
            ReadReport(_hash(read.get("sod_sha256"), "sod_sha256"), groups), CryptoReport(**{k: str(v) for k, v in crypto.items()}),
        )  # fmt: skip
    except KeyError as e:
        raise CrosscheckError("RUN_FILE_INVALID", f"missing {e.args[0]}") from e


def pair_runs(runs: Iterable[ChipRun]) -> tuple[list[Comparison], list[str]]:
    """Pair each independent reader's run with our run of the same document, device and access method. Returns the comparisons and the runs that found no partner (a lab sees what is missing)."""
    ours: dict[tuple[str, str, str], ChipRun] = {}
    references: list[ChipRun] = []
    for r in runs:
        if r.reader == "ZOIKO":
            ours[(r.document_ref, r.device_model, r.access)] = r
        else:
            references.append(r)
    comparisons: list[Comparison] = []
    unpaired: list[str] = []
    for ref in references:
        mine = ours.get((ref.document_ref, ref.device_model, ref.access))
        if mine is None:
            unpaired.append(f"{ref.reader}:{ref.document_ref}:{ref.device_model}:{ref.access}")
        else:
            comparisons.append(compare(mine, ref))
    return comparisons, unpaired
