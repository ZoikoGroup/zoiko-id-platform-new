"""Commercial capability bands C1-C5 and the feature entitlement decision (ZID-ENG-DEC-008, proposed). Pure domain, no I/O.

A plan authorizes commercial capabilities and quotas, nothing else: roles, trust, privacy, residency, step-up and approval are
independent gates that a plan can neither satisfy nor waive. The protected baseline (registration and recovery, MFA and passkeys,
the five roles, tenant isolation, audit integrity, sanctions and trust holds, regional placement, high-risk dual control) is never
gated by a plan, a billing state or a suspension, and `validate` fails the load if a matrix ever makes it so.

The Commercial Estate (ZID-COM-001) stays authoritative for plan terms. Until the owners switch the band matrix to ENFORCE it is
reported against, not applied: `reconcile` lists every place the Estate and this record disagree, so nothing is decided silently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

BANDS = ("C1", "C2", "C3", "C4", "C5")

# The eight rows the record marks mandatory in every band; the load fails if any is missing or is not mandatory everywhere.
BASELINE_IDS = (
    "baseline.secure-account-registration-recovery",
    "baseline.mfa-passkeys-session-protection",
    "baseline.five-standard-tenant-roles-q7",
    "baseline.tenant-isolation-access-review-minimum",
    "baseline.mandatory-audit-evidence-integrity",
    "baseline.mandatory-sanctions-trust-holds",
    "baseline.regional-placement-policy-enforcement",
    "baseline.high-risk-dual-control-safeguards",
)

# What a downgrade, a cancellation or a suspension never removes (record section 06).
PRESERVED = (
    "identity proofs",
    "issued credentials",
    "the evidence ledger and audit history",
    "legal holds and retention",
    "credential revocation and public validity handling",
    "security, recovery and privacy-access paths",
)


# Reasons that are about where or whether a route may be used, never about what was bought. An upgrade is not offered for them:
# "never present a paid upgrade as a workaround for compliance holds" (record section 06).
COMPLIANCE_REASONS = frozenset(
    {"NOT_AVAILABLE_IN_REGION", "COVERAGE_UNKNOWN", "PROVIDER_ROUTE_OUT_OF_COVERAGE"}
)


class BandError(ValueError):
    pass


class Cell(StrEnum):
    BASELINE = "M"
    INCLUDED = "I"
    ADD_ON = "A"
    NONE = "-"


class State(StrEnum):
    BASELINE = "BASELINE"
    INCLUDED = "INCLUDED"
    ADD_ON = "ADD_ON"
    UPGRADE = "UPGRADE"  # "Available with upgrade"


LABEL = {
    State.BASELINE: "Included",
    State.INCLUDED: "Included",
    State.ADD_ON: "Available as an add-on",
    State.UPGRADE: "Available with upgrade",
}
NOT_IN_REGION = "Not available in your region"

_RANK = {Cell.NONE: 0, Cell.ADD_ON: 1, Cell.INCLUDED: 2, Cell.BASELINE: 3}


@dataclass(frozen=True, slots=True)
class Feature:
    feature_id: str
    section: str  # baseline | standard | advanced
    name: str
    cells: dict[str, Cell]
    note: str
    catalogue: str | None  # the Commercial Estate capability that carries it today


@dataclass(frozen=True, slots=True)
class BandMatrix:
    version: str
    plan_of_band: dict[str, str]
    enforcement: str  # REPORT_ONLY | ENFORCE
    features: dict[str, Feature]
    snapshot_ttl_seconds: int = 60

    def band_of_plan(self, plan_code: str) -> str | None:
        return next((b for b, p in self.plan_of_band.items() if p == plan_code), None)


def build(
    version: str,
    plan_of_band: dict[str, str],
    enforcement: str,
    features: list[Feature],
    snapshot_ttl_seconds: int = 60,
) -> BandMatrix:
    by_id: dict[str, Feature] = {}
    for f in features:
        if f.feature_id in by_id:
            raise BandError(f"duplicate feature {f.feature_id}")
        by_id[f.feature_id] = f
    m = BandMatrix(version, plan_of_band, enforcement, by_id, snapshot_ttl_seconds)
    validate(m)
    return m


def validate(m: BandMatrix) -> None:
    if not m.version or not m.features:
        raise BandError("an empty matrix is not a policy")
    if tuple(m.plan_of_band) != BANDS or len(set(m.plan_of_band.values())) != len(BANDS):
        raise BandError("the five bands map to five different plans")
    if m.enforcement not in ("REPORT_ONLY", "ENFORCE"):
        raise BandError("band_matrix_enforcement is REPORT_ONLY or ENFORCE")
    if not 1 <= m.snapshot_ttl_seconds <= 300:
        raise BandError("a cached snapshot lives at most five minutes")
    for fid in BASELINE_IDS:
        f = m.features.get(fid)
        if f is None or any(f.cells.get(b) is not Cell.BASELINE for b in BANDS):
            raise BandError(f"{fid}: the protected baseline is mandatory in every band")
    for f in m.features.values():
        if tuple(f.cells) != BANDS:
            raise BandError(f"{f.feature_id}: every feature names the five bands in order")
        mandatory = [b for b in BANDS if f.cells[b] is Cell.BASELINE]
        if mandatory and (len(mandatory) != len(BANDS) or f.section != "baseline"):
            raise BandError(
                f"{f.feature_id}: only the baseline section is mandatory, and in every band"
            )
        if f.section == "baseline" and not mandatory:
            raise BandError(f"{f.feature_id}: a baseline row is mandatory")
        ranks = [_RANK[f.cells[b]] for b in BANDS]
        for lower, higher, band in zip(ranks, ranks[1:], BANDS[1:], strict=False):
            # higher bands inherit lower-band capabilities; an add-on is contracted usage above the included bands
            if lower == _RANK[Cell.INCLUDED] and higher < _RANK[Cell.INCLUDED]:
                raise BandError(f"{f.feature_id}: {band} must keep what the band below includes")
            if lower == _RANK[Cell.ADD_ON] and higher < _RANK[Cell.ADD_ON]:
                raise BandError(f"{f.feature_id}: {band} must keep what the band below offers")


def state_of(feature: Feature, band: str) -> State:
    cell = feature.cells[band]
    return {
        Cell.BASELINE: State.BASELINE,
        Cell.INCLUDED: State.INCLUDED,
        Cell.ADD_ON: State.ADD_ON,
        Cell.NONE: State.UPGRADE,
    }[cell]


def lowest_band(feature: Feature) -> str | None:
    """The first band that includes the feature, for an "Available with upgrade" message."""
    return next(
        (b for b in BANDS if feature.cells[b] in (Cell.BASELINE, Cell.INCLUDED, Cell.ADD_ON)), None
    )


# ----------------------------------------------------------------------------------------- decision


@dataclass(frozen=True, slots=True)
class EstateView:
    """What the Commercial Estate's own gate said about the mapped capability (None when the feature has no Estate capability)."""

    allowed: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    feature_id: str
    outcome: str  # ALLOW | DENY | HOLD
    state: State | None
    reasons: tuple[str, ...]
    band: str | None
    divergent: bool  # the band matrix and the Estate disagree about this tenant right now
    upgrade_band: str | None = None
    catalog_version: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome == "ALLOW"


def decide_feature(
    m: BandMatrix,
    feature_id: str,
    plan_code: str,
    *,
    estate: EstateView | None,
    contract_active: bool = False,
    subscription_state: str = "ACTIVE",
    region_ok: bool | None = True,
    coverage_ok: bool | None = True,
) -> FeatureDecision:
    """The entitlement step of the authoritative order (record section 05, step 5). Every earlier step (identity, holds, residency,
    role, assurance) is the caller's, and a plan can never stand in for one of them.

    An unknown fact is a HOLD, an unknown feature, a failed fact or a missing entitlement a DENY, and none of it is "verified"."""
    feature = m.features.get(feature_id)
    band = m.band_of_plan(plan_code)

    def out(
        outcome: str, *reasons: str, state: State | None = None, divergent: bool = False
    ) -> FeatureDecision:
        commercial = not (set(reasons) & COMPLIANCE_REASONS)
        up = lowest_band(feature) if feature and state is State.UPGRADE and commercial else None
        return FeatureDecision(
            feature_id, outcome, state, tuple(reasons), band, divergent, up, m.version
        )

    if feature is None:
        return out("DENY", "UNKNOWN_FEATURE")
    if band is None:
        return out("HOLD", "PLAN_NOT_MAPPED_TO_A_BAND")
    state = state_of(feature, band)
    if state is State.BASELINE:  # never gated by a plan, a billing state or a suspension
        return out("ALLOW", state=state)
    if region_ok is False:
        return out("DENY", "NOT_AVAILABLE_IN_REGION", state=state)
    if region_ok is None or coverage_ok is None:
        return out("HOLD", "COVERAGE_UNKNOWN", state=state)
    if coverage_ok is False:
        return out("HOLD", "PROVIDER_ROUTE_OUT_OF_COVERAGE", state=state)
    if subscription_state == "SUSPENDED":
        return out("DENY", "SUSPENDED", state=state)
    band_allows = state is State.INCLUDED or (state is State.ADD_ON and contract_active)
    band_reason: str | None = None
    if not band_allows:
        band_reason = "ADD_ON_REQUIRED" if state is State.ADD_ON else "NOT_IN_BAND"
    if estate is None:  # no Estate capability carries it: the band matrix alone decides
        return (
            out("ALLOW", state=state)
            if band_allows
            else out("DENY", band_reason or "NOT_IN_BAND", state=state)
        )
    divergent = band_allows != estate.allowed
    if not estate.allowed:
        return out("DENY", estate.reason or "NOT_IN_PLAN", state=state, divergent=divergent)
    if not band_allows and m.enforcement == "ENFORCE":
        return out("DENY", band_reason or "NOT_IN_BAND", state=state, divergent=divergent)
    return out("ALLOW", state=state, divergent=divergent)


# --------------------------------------------------------------------------- reconciliation


@dataclass(frozen=True, slots=True)
class Divergence:
    feature_id: str
    band: str
    plan: str
    band_cell: str
    estate_mode: str
    kind: str


_ESTATE_INCLUDES = {"INCLUDED", "METERED"}
_ESTATE_PAID = {"ADD_ON", "CONTRACT", "QUALIFIED"}


def reconcile(m: BandMatrix, estate: Mapping[str, Mapping[str, str]]) -> list[Divergence]:
    """Every place the Commercial Estate's plan capabilities and this record's band matrix disagree. `estate` maps plan code to
    {capability key: mode}. The Estate governs until the owners say otherwise; this is the list they rule on."""
    out: list[Divergence] = []
    for f in m.features.values():
        if f.catalogue is None:
            continue
        for band in BANDS:
            plan = m.plan_of_band[band]
            mode = estate.get(plan, {}).get(f.catalogue)
            if mode is None:
                continue
            cell = f.cells[band]
            kind: str | None = None
            if cell is Cell.BASELINE and mode not in _ESTATE_INCLUDES:
                kind = "BASELINE_NOT_INCLUDED_BY_ESTATE"
            elif (
                cell is Cell.INCLUDED
                and mode not in _ESTATE_INCLUDES
                or cell is Cell.ADD_ON
                and mode == "NONE"
            ):
                kind = "ESTATE_STRICTER"
            elif (
                cell is Cell.NONE
                and (mode in _ESTATE_INCLUDES)
                or cell is Cell.ADD_ON
                and mode in _ESTATE_INCLUDES
            ):
                kind = "ESTATE_MORE_GENEROUS"
            if kind:
                out.append(Divergence(f.feature_id, band, plan, cell.value, mode, kind))
    return out


# ----------------------------------------------------------------------------------- downgrade


@dataclass(frozen=True, slots=True)
class DowngradeImpact:
    from_band: str
    to_band: str
    becomes_inactive: tuple[str, ...]  # feature ids the customer loses
    becomes_add_on: tuple[str, ...]  # still available, but only under a contract
    preserved: tuple[str, ...] = field(default=PRESERVED)
    effective: str = "AT_AUTHORIZED_RENEWAL"
    deletes_anything: bool = False


def downgrade_impact(m: BandMatrix, from_band: str, to_band: str) -> DowngradeImpact:
    """What a move to a lower band switches off, disclosed before it happens. It never deletes proof, credentials or evidence:
    a feature that becomes inactive stops accepting new work, and what it produced stays."""
    if from_band not in BANDS or to_band not in BANDS:
        raise BandError("unknown band")
    gone: list[str] = []
    contracted: list[str] = []
    for f in m.features.values():
        before, after = f.cells[from_band], f.cells[to_band]
        if _RANK[after] >= _RANK[before]:
            continue
        if after is Cell.NONE:
            gone.append(f.feature_id)
        elif after is Cell.ADD_ON:
            contracted.append(f.feature_id)
    return DowngradeImpact(from_band, to_band, tuple(gone), tuple(contracted))
