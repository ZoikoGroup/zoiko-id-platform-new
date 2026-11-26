"""The SDK release register and the rules that keep it honest (ZID-CP-WF10 v2.0, WF-10.06; CR-173). Pure domain, no I/O.

An SDK count is not a quality measure. What matters to a partner is whether a package is real, tested, signed and known to work where they run it. So the register
records those facts per package, and this module decides what may be said from them:

- GA needs a published package, a signed release with a software bill of materials, an executed test run no older than ninety days with its evidence reference, and
  at least one supported platform backed by executed evidence;
- BETA needs a published package; EXPERIMENTAL needs nothing and promises nothing;
- a platform may be listed as supported only with the evidence reference of an executed run;
- the "download" button exists only for a package whose state is GA and has no problem; a beta may offer "try the beta"; anything else offers only "view the source";
- a package whose register entry breaks a rule is shown with the rule it breaks, and its button is the weakest one.

Nothing here says a package works. The register is a record of what has been proved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

STATES = ("EXPERIMENTAL", "BETA", "GA", "DEPRECATED")
AUDIENCES = ("PARTNER_SERVER", "CAPTURE", "TOOL_SERVER", "GATEWAY")
TEST_FRESH_FOR = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class Sdk:
    id: str
    name: str
    language: str
    audience: str
    purpose: str
    state: str
    version: str | None
    published: bool
    repo_path: str
    tests_path: str
    last_test_run: date | None
    last_test_evidence: str | None
    signed: bool
    sbom: bool
    compatibility: tuple[Mapping[str, Any], ...]


def parse(raw: Mapping[str, Any]) -> Sdk:
    prov = raw.get("provenance") or {}
    last = raw.get("last_test_run")
    return Sdk(
        id=str(raw["id"]), name=str(raw["name"]), language=str(raw["language"]), audience=str(raw["audience"]), purpose=str(raw.get("purpose", "")), state=str(raw["state"]),
        version=str(raw["version"]) if raw.get("version") is not None else None, published=bool(raw.get("published", False)), repo_path=str(raw["repo_path"]), tests_path=str(raw["tests_path"]),
        last_test_run=last if isinstance(last, date) else (date.fromisoformat(str(last)) if last else None), last_test_evidence=raw.get("last_test_evidence"),
        signed=bool(prov.get("signed", False)), sbom=bool(prov.get("sbom", False)), compatibility=tuple(raw.get("compatibility") or ()),
    )  # fmt: skip


def problems(s: Sdk, today: date) -> list[str]:
    """Every rule this entry breaks. An empty list means its stated state is backed by its evidence."""
    out: list[str] = []
    if s.state not in STATES:
        out.append("STATE_UNKNOWN")
    if s.audience not in AUDIENCES:
        out.append("AUDIENCE_UNKNOWN")
    unbacked = [c for c in s.compatibility if c.get("supported") and not c.get("evidence_ref")]
    if unbacked:
        out.append("SUPPORTED_PLATFORM_WITHOUT_EXECUTED_EVIDENCE")
    if s.state in ("BETA", "GA") and not s.published:
        out.append("NOT_PUBLISHED")
    if s.state == "GA":
        if not (s.signed and s.sbom):
            out.append("GA_NEEDS_SIGNED_RELEASE_AND_SBOM")
        if s.last_test_run is None or not s.last_test_evidence:
            out.append("GA_NEEDS_AN_EXECUTED_TEST_RUN_WITH_EVIDENCE")
        elif today - s.last_test_run > TEST_FRESH_FOR:
            out.append("GA_TEST_RUN_IS_STALE")
        if not any(c.get("supported") and c.get("evidence_ref") for c in s.compatibility):
            out.append("GA_NEEDS_A_SUPPORTED_PLATFORM_WITH_EVIDENCE")
    if s.published and not s.version:
        out.append("PUBLISHED_WITHOUT_A_VERSION")
    return sorted(set(out))


def cta(s: Sdk, today: date) -> str:
    """The strongest button this package has earned, never more."""
    if problems(s, today):
        return "VIEW_SOURCE"
    if s.state == "GA":
        return "DOWNLOAD"
    if s.state == "BETA":
        return "TRY_BETA"
    return "VIEW_SOURCE"


def view(sdks: Sequence[Sdk], today: date) -> dict[str, Any]:
    items = [
        {
            "id": s.id, "name": s.name, "language": s.language, "audience": s.audience, "purpose": s.purpose, "state": s.state, "version": s.version, "published": s.published,
            "last_test_run": s.last_test_run.isoformat() if s.last_test_run else None, "last_test_evidence": s.last_test_evidence, "signed": s.signed, "sbom": s.sbom,
            "supported_platforms": [str(c.get("platform")) for c in s.compatibility if c.get("supported") and c.get("evidence_ref")],
            "problems": problems(s, today), "action": cta(s, today),
        }
        for s in sdks
    ]  # fmt: skip
    ga = [i["id"] for i in items if i["state"] == "GA" and not i["problems"]]
    return {
        "as_of": today.isoformat(), "items": items, "ga_count": len(ga),
        "statement": "No package is generally available." if not ga else f"{len(ga)} package(s) are generally available, each with a signed release, a bill of materials and a current executed test run.",
        "note": "This register records what has been proved. A package that is not GA is not offered for download.",
    }  # fmt: skip
