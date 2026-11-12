"""The assurance plane's use cases (ZID-UAT-005; CR-074 slices S2 to S5). Each runs inside the caller's tenant transaction.

The rules are in `domain/rules.py`; this layer adds storage, the evidence signing key and events. Two things it never does: mark a case PASS on
anyone's say-so (a PASS exists only as the verdict of a complete signed manifest from the run's own tester), and let a release be GO without the
rules saying so over case results taken from those manifests.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, sign_jws, verify_jws
from zid_outbox import emit

from zid_platform.modules.assurance.domain import rules
from zid_platform.modules.assurance.domain.rules import RuleError, Status
from zid_platform.modules.assurance.infrastructure import store
from zid_platform.modules.evidence.ports import current_runtime
from zid_platform.modules.privacy.ports import release_gate
from zid_platform.modules.verification.ports import EntitlementNotFound, change_entitlement_status

MANIFEST_TYP = "zid-uat-manifest+jwt"
DECISION_TYP = "zid-uat-decision+jwt"
SUITES = ("GOV", "IDV", "DOC", "EDU", "PRO", "ORG", "FIN", "VC", "RNT", "INT", "PRI", "OPS")
AUDIENCES = ("auditor", "regulator", "customer")


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _announce(
    conn: AsyncConnection,
    tenant: str,
    name: str,
    aggregate: str,
    ident: str,
    data: Mapping[str, Any],
) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=tenant,
        aggregate_type=aggregate,
        aggregate_id=ident,
        aggregate_version=await store.next_event_version(conn, aggregate, ident),
        data=dict(data),
    )


# ------------------------------------------------------------------------------------------------------------------------ roles and baseline


async def assign_role(
    conn: AsyncConnection, tenant: str, actor: str, *, role: str, principal: str
) -> dict[str, Any]:
    if actor == principal:
        raise RuleError("ROLE_SELF_ASSIGNMENT", "nobody assigns an assurance role to themselves")
    if role in rules.RELEASE_ROLES:
        held = (await store.roles_of(conn, principal)) & set(rules.RELEASE_ROLES)
        if held and role not in held:
            raise RuleError("ONE_PERSON_ONE_ROLE", f"already holds {min(held)}")
    ident = await store.assign_role(conn, tenant, role=role, principal=principal, by=actor)
    return {"assignment_id": ident, "role": role, "principal": principal}


async def import_baseline(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    version: str,
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not cases:
        raise RuleError("BASELINE_INVALID", "no cases")
    seen: set[str] = set()
    for c in cases:
        cid = str(c.get("case_id", ""))
        prefix = cid.split("-")[0]
        if prefix not in SUITES or c.get("suite") != prefix or cid in seen:
            raise RuleError(
                "BASELINE_INVALID", f"case {cid!r} is unknown, mis-suited or listed twice"
            )
        seen.add(cid)
    digest = _sha(rules.canonical(sorted(cases, key=lambda c: c["case_id"])))
    baseline_id = await store.insert_baseline(
        conn, tenant, version=version, source_sha256=digest, cases=cases, by=actor
    )
    return {
        "baseline_id": baseline_id,
        "version": version,
        "case_count": len(cases),
        "source_sha256": digest,
    }


async def requirements(
    conn: AsyncConnection, *, suite: str | None = None, status: str | None = None
) -> dict[str, Any]:
    base = await store.latest_baseline(conn)
    if base is None:
        return {"baseline": None, "cases": [], "counts": {}}
    verdicts = await store.latest_any(conn)
    dev = await store.latest_any(conn, "DEVELOPER")
    out = []
    for c in await store.baseline_cases(conn, str(base["baseline_id"])):
        st = verdicts.get(c["case_id"], {}).get("verdict", Status.NOT_RUN.value)
        if (suite and c["suite"] != suite) or (status and st != status):
            continue
        out.append(
            {
                **c,
                "developer_evidence": {
                    "manifest_id": str(dev[c["case_id"]]["manifest_id"]),
                    "verdict": dev[c["case_id"]]["verdict"],
                    "counts_toward_release": False,
                }
                if c["case_id"] in dev
                else None,
                "status": st,
                "manifest_id": str(verdicts[c["case_id"]]["manifest_id"])
                if c["case_id"] in verdicts
                else None,
            }
        )
    counts: dict[str, int] = {}
    for c in out:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "baseline": {
            "baseline_id": str(base["baseline_id"]),
            "version": base["version"],
            "source_sha256": base["source_sha256"],
        },
        "cases": out,
        "counts": counts,
    }


# ------------------------------------------------------------------------------------------------------------------------------- fixtures


async def create_fixture(
    conn: AsyncConnection, tenant: str, actor: str, *, name: str, content_sha256: str
) -> dict[str, Any]:
    ids = await store.insert_fixture(
        conn, tenant, name=name, content_sha256=content_sha256, author=actor
    )
    return {**ids, "revision": 1, "state": "DRAFT"}


async def add_revision(
    conn: AsyncConnection, tenant: str, actor: str, *, fixture_id: str, content_sha256: str
) -> dict[str, Any]:
    revs = await store.fixture_revisions(conn, fixture_id)
    if not revs:
        raise RuleError("FIXTURE_NOT_FOUND")
    last = revs[-1]
    if (
        rules.amend_fixture(
            state=last["state"], old_hash=last["content_sha256"], new_hash=content_sha256
        )
        == "UNCHANGED"
    ):
        raise RuleError(
            "REVISION_IS_IDENTICAL",
            "an approved fixture is locked; a change is a new revision with a new hash",
        )
    ident = await store.add_revision(
        conn,
        tenant,
        fixture_id=fixture_id,
        revision=int(last["revision"]) + 1,
        content_sha256=content_sha256,
        author=actor,
    )
    return {
        "fixture_id": fixture_id,
        "revision_id": ident,
        "revision": int(last["revision"]) + 1,
        "state": "DRAFT",
    }


async def review_fixture(
    conn: AsyncConnection,
    actor: str,
    *,
    fixture_id: str,
    revision: int,
    approve: bool,
    rationale: str,
) -> dict[str, Any]:
    rev = next(
        (
            r
            for r in await store.fixture_revisions(conn, fixture_id)
            if int(r["revision"]) == revision
        ),
        None,
    )
    if rev is None:
        raise RuleError("REVISION_NOT_FOUND")
    if rev["state"] != "DRAFT":
        raise RuleError(
            "REVISION_ALREADY_REVIEWED", "a reviewed revision is final; a change is a new revision"
        )
    state = rules.review_fixture(
        author=str(rev["author"]), reviewer=actor, approve=approve, rationale=rationale
    )
    await store.review_revision(
        conn, str(rev["revision_id"]), state=state, reviewer=actor, rationale=rationale
    )
    return {
        "fixture_id": fixture_id,
        "revision": revision,
        "state": state,
        "content_sha256": rev["content_sha256"],
    }


# ----------------------------------------------------------------------------------------------------------------------------------- runs


async def create_run(
    conn: AsyncConnection, tenant: str, actor: str, *, release_unit: Mapping[str, Any], environment_id: str, build_sha256: str,
    config_digest: str, case_ids: Sequence[str], fixture_hashes: Sequence[str], commit_authors: Sequence[str],
    pins: Mapping[str, str] | None = None,
    kind: str = "INDEPENDENT",
) -> dict[str, Any]:  # fmt: skip
    if kind not in ("INDEPENDENT", "DEVELOPER"):
        raise RuleError("RUN_KIND_UNKNOWN", kind)
    bad_pins = sorted(set(pins or {}) - set(rules.WATCHED))
    if bad_pins:
        raise RuleError("PIN_NOT_WATCHED", ", ".join(bad_pins))
    # a DEVELOPER run is the team's own drill: it may be run by the people who wrote the build, is labeled as such everywhere, and never counts toward a verdict
    if kind == "INDEPENDENT":
        if "TESTER" not in await store.roles_of(conn, actor):
            raise RuleError("TESTER_ROLE_REQUIRED", "only an assigned tester starts a run")
        if actor in set(commit_authors):
            raise RuleError(
                "TESTER_NOT_INDEPENDENT", "a commit author of the build under test cannot test it"
            )
    base = await store.latest_baseline(conn)
    if base is None:
        raise RuleError("NO_BASELINE")
    known = {c["case_id"] for c in await store.baseline_cases(conn, str(base["baseline_id"]))}
    unknown = sorted(set(case_ids) - known)
    if unknown or not case_ids:
        raise RuleError("CASE_NOT_IN_BASELINE", ", ".join(unknown) or "no cases")
    unmatched = sorted(set(fixture_hashes) - await store.approved_hashes(conn))
    if unmatched:
        raise RuleError("FIXTURE_NOT_APPROVED", "a run uses only fixtures a second person approved")
    run_id = await store.insert_run(
        conn, tenant,
        {
            "baseline_id": str(base["baseline_id"]), "release_unit": dict(release_unit), "environment_id": environment_id,
            "build_sha256": build_sha256, "config_digest": config_digest, "test_set_sha256": _sha(",".join(sorted(case_ids))),
            "fixture_hashes": list(fixture_hashes), "pins": dict(pins or {}), "tester_principal": actor, "tester_role": "TESTER" if kind == "INDEPENDENT" else "DEVELOPER", "kind": kind,
        },
    )  # fmt: skip
    return {
        "run_id": run_id,
        "baseline_id": str(base["baseline_id"]),
        "test_set_sha256": _sha(",".join(sorted(case_ids))),
        "frozen": True,
        "kind": kind,
    }


async def _own_run(conn: AsyncConnection, actor: str, run_id: str) -> dict[str, Any]:
    run = await store.get_run(conn, run_id)
    if run is None:
        raise RuleError("RUN_NOT_FOUND")
    if str(run["tester_principal"]) != actor:
        raise RuleError(
            "NOT_THE_RUNS_TESTER", "only the run's own tester records its events and manifests"
        )
    return run


async def append_event(
    conn: AsyncConnection, tenant: str, actor: str, *, run_id: str, case_id: str | None, kind: str, payload: Mapping[str, Any], idem_key: str
) -> dict[str, Any]:  # fmt: skip
    await _own_run(conn, actor, run_id)
    unsafe = rules.raw_unsafe_keys(payload)
    if unsafe:
        raise RuleError("PAYLOAD_NOT_RAW_SAFE", ", ".join(unsafe))
    existing = await store.event_by_key(conn, run_id, idem_key)
    if existing is not None:
        if (
            existing["payload"] != dict(payload)
            or existing["kind"] != kind
            or existing["case_id"] != case_id
        ):
            raise RuleError("IDEMPOTENCY_KEY_REUSED", "the same key carried a different event")
        return {"seq": int(existing["seq"]), "hash": existing["hash"], "replayed": True}
    last = await store.last_event(conn, run_id)
    seq, prev = (int(last["seq"]) + 1, last["hash"]) if last else (1, rules.GENESIS)
    h = rules.chain_hash(prev, seq, case_id, kind, payload)
    await store.insert_event(
        conn,
        tenant,
        run_id=run_id,
        seq=seq,
        case_id=case_id,
        kind=kind,
        payload=payload,
        prev_hash=prev,
        hash_=h,
        idem_key=idem_key,
    )
    return {"seq": seq, "hash": h, "replayed": False}


async def open_defect(
    conn: AsyncConnection, tenant: str, actor: str, *, case_id: str, severity: str, owner: str
) -> dict[str, Any]:
    ident = await store.insert_defect(
        conn, tenant, case_id=case_id, severity=severity, owner=owner, by=actor
    )
    return {"defect_id": ident, "case_id": case_id, "severity": severity, "status": "OPEN"}


async def sign_manifest(
    conn: AsyncConnection, tenant: str, actor: str, *, run_id: str, case_id: str, result: str, requirement_ids: Sequence[str],
    trace_refs: Sequence[str], provider_receipt_refs: Sequence[str], source_snapshot_refs: Sequence[str], oracle_version: str,
    defect_id: str | None, human_approval_refs: Sequence[str], measurements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    run = await _own_run(conn, actor, run_id)
    claimed = result.upper()
    if claimed not in {s.value for s in Status}:
        raise RuleError("RESULT_UNKNOWN", result)
    if claimed == Status.FAIL.value:
        d = await store.get_defect(conn, defect_id) if defect_id else None
        if d is None or d["case_id"] != case_id:
            raise RuleError("FAIL_NEEDS_A_DEFECT", "raise a defect for this case first")
    policy_check = None
    if measurements and run["kind"] == "DEVELOPER":
        policy_check = {
            "policy_id": None,
            "breaches": [],
            "note": "developer drill: recorded, not judged; only an independent run is judged against the signed policy",
        }
    elif measurements:
        policy_check = await check_measurements(conn, run["release_unit"], measurements)
        if policy_check["breaches"] and claimed == Status.PASS.value:
            raise RuleError("MEASUREMENTS_BREACH_THE_POLICY", ", ".join(policy_check["breaches"]))
    signer = current_runtime().signer
    last = await store.last_event(conn, run_id)
    head = last["hash"] if last else rules.GENESIS
    ru = run["release_unit"]
    base = {
        "run_id": run_id, "case_id": case_id, "requirement_ids": list(requirement_ids), "build_sha256": run["build_sha256"],
        "infra_ref": run["environment_id"], "tester_principal": actor, "tenant_id": tenant, "country": ru.get("country"),
        "policy_pack": ru.get("policy_pack"), "fixture_hashes": list(run["fixture_hashes"]), "oracle_version": oracle_version, "result": claimed,
        "captured_at": _now().isoformat(), "evidence_signing_key_id": signer.kid, "trace_refs": list(trace_refs),
        "provider_receipt_refs": list(provider_receipt_refs), "source_snapshot_refs": list(source_snapshot_refs),
        "human_approval_refs": list(human_approval_refs), "defects": [defect_id] if defect_id else [],
        "measurements": dict(measurements or {}), "policy_check": policy_check, "run_kind": run["kind"],
    }  # fmt: skip
    digest = rules.manifest_digest(base)
    jws = sign_jws(
        signer,
        {
            "manifest_sha256": digest,
            "run_id": run_id,
            "case_id": case_id,
            "result": claimed,
            "chain_head": head,
        },
        typ=MANIFEST_TYP,
    )
    manifest = {**base, "manifest_signature": jws.split(".")[2]}
    verdict = rules.verdict_from(manifest)
    problems = rules.manifest_problems(manifest)
    ident = await store.insert_manifest(
        conn, tenant, run_id=run_id, case_id=case_id, claimed=claimed, verdict=verdict.value, manifest=manifest, manifest_sha256=digest,
        problems=problems, jws=jws, kid=signer.kid, chain_head=head,
    )  # fmt: skip
    return {
        "manifest_id": ident, "case_id": case_id, "claimed_result": claimed, "verdict": verdict.value, "problems": problems,
        "manifest_sha256": digest, "jws": jws, "chain_head": head,
        "note": "The verdict is what the manifest supports. An incomplete manifest that claims PASS is INCONCLUSIVE.",
    }  # fmt: skip


def _redact(m: Mapping[str, Any], audience: str) -> dict[str, Any]:
    base = {
        "manifest_id": str(m["manifest_id"]),
        "case_id": m["case_id"],
        "verdict": m["verdict"],
        "manifest_sha256": m["manifest_sha256"],
    }
    if audience == "customer":
        return base
    body = dict(m["manifest"])
    if audience == "regulator":
        body.pop("tester_principal", None)
        body.pop("infra_ref", None)
    return {**base, "problems": list(m["problems"]), "manifest": body, "jws": m["jws"]}


async def evidence_view(conn: AsyncConnection, *, run_id: str, audience: str) -> dict[str, Any]:
    if audience not in AUDIENCES:
        raise RuleError("AUDIENCE_UNKNOWN", audience)
    run = await store.get_run(conn, run_id)
    if run is None:
        raise RuleError("RUN_NOT_FOUND")
    events = await store.events_of(conn, run_id)
    return {
        "run_id": run_id, "audience": audience, "build_sha256": run["build_sha256"], "events": len(events),
        "chain_head": events[-1]["hash"] if events else rules.GENESIS, "chain_problems": rules.verify_chain(events),
        "manifests": [_redact(m, audience) for m in await store.manifests_of_run(conn, run_id)],
        "note": "References and digests only: no biometric, document or claim value is held here.",
    }  # fmt: skip


async def verify_bundle(
    conn: AsyncConnection, tenant: str, *, manifest: Mapping[str, Any], jws: str, events: Sequence[Mapping[str, Any]] | None, manifest_id: str | None
) -> dict[str, Any]:  # fmt: skip
    """Third-party style check of an exported manifest bundle. Anything wrong makes it invalid, and a stored manifest that fails locks the releases built on it."""
    problems: list[str] = []
    payload: Mapping[str, Any] = {}
    try:
        _h, payload = verify_jws(jws, keys=current_runtime().trusted_keys(), typ=MANIFEST_TYP)
    except CryptoError:
        problems.append("SIGNATURE_INVALID")
    if payload:
        if payload.get("manifest_sha256") != rules.manifest_digest(manifest):
            problems.append("MANIFEST_CHANGED")
        if manifest.get("manifest_signature") != jws.split(".")[2]:
            problems.append("SIGNATURE_FIELD_MISMATCH")
        if events is not None:
            problems += rules.verify_chain(events)
            heads = {rules.GENESIS, *(e["hash"] for e in events)}
            if payload.get("chain_head") not in heads:
                problems.append("CHAIN_HEAD_MISSING")
    locked: list[str] = []
    if problems and manifest_id:
        for r in await store.runs_for_manifest(conn, manifest_id):
            rid = str(r["release_id"])
            await store.set_candidate_status(conn, rid, "LOCKED", "TAMPERED_EVIDENCE")
            locked.append(rid)
        await _announce(
            conn,
            tenant,
            "assurance.evidence.tampered",
            "uat_manifest",
            manifest_id,
            {"manifest_id": manifest_id, "problems": problems, "locked_releases": locked},
        )
    return {"valid": not problems, "problems": problems, "locked_releases": locked}


async def verify_stored(conn: AsyncConnection, tenant: str, manifest_id: str) -> dict[str, Any]:
    m = await store.get_manifest(conn, manifest_id)
    if m is None:
        raise RuleError("MANIFEST_NOT_FOUND")
    events = await store.events_of(conn, str(m["run_id"]))
    return await verify_bundle(
        conn, tenant, manifest=m["manifest"], jws=m["jws"], events=events, manifest_id=manifest_id
    )


# --------------------------------------------------------------------------------------------------------------------- release candidates


def _candidate(row: Mapping[str, Any], signatures: Sequence[Mapping[str, Any]]) -> rules.Candidate:
    facts = row["facts"]
    snap = row["snapshot"]["cases"]
    na = set(facts.get("not_applicable", []))
    return rules.Candidate(
        cases=[
            rules.Case(cid, v["suite"], v["risk"], Status(v["status"]), applicable=cid not in na)
            for cid, v in sorted(snap.items())
        ],
        unresolved_requirement_links=facts.get("unresolved_requirement_links"),
        official_source_status_in_date=facts.get("official_source_status_in_date"),
        critical_false_pass_open=facts.get("critical_false_pass_open"),
        open_critical_or_high_security=facts.get("open_critical_or_high_security"),
        privacy_release_ready=facts.get("privacy_release_ready"),
        thresholds_policy_signed=bool(facts.get("thresholds_policy_signed", False)),
        country_legal_required=bool(facts.get("country_legal_required", True)),
        signatures=[
            rules.Signature(s["role"], str(s["principal"]), s["decision"]) for s in signatures
        ],
        engineering_owner=str(row["engineering_owner"]) if row["engineering_owner"] else None,
        commit_authors=set(row["commit_authors"]),
        flag_documented=bool(facts.get("flag_documented", False)),
        post_launch_owner=facts.get("post_launch_owner"),
        no_go_facts=facts.get("no_go_facts", {}),
    )


def _decision_view(d: rules.Decision) -> dict[str, Any]:
    return {
        "status": d.status, "reason": d.reason, "p0_required": d.p0_required, "p0_pass": d.p0_pass, "open_critical": d.open_critical,
        "missing": list(d.missing), "gates": [{"gate": g.gate, "passed": g.passed, "reasons": list(g.reasons)} for g in d.gates],
    }  # fmt: skip


async def create_candidate(
    conn: AsyncConnection, tenant: str, actor: str, *, release_unit: Mapping[str, Any], build_sha256: str, policy_pack: str,
    facts: Mapping[str, Any], engineering_owner: str | None, commit_authors: Sequence[str], privacy_gate: Mapping[str, str] | None,
    current_inputs: Mapping[str, str] | None = None,
) -> dict[str, Any]:  # fmt: skip
    base = await store.latest_baseline(conn)
    if base is None:
        raise RuleError("NO_BASELINE")
    cases = await store.baseline_cases(conn, str(base["baseline_id"]))
    latest = await store.latest_manifests(conn, release_unit)
    snapshot: dict[str, Any] = {}
    expired: list[str] = []
    for c in cases:
        m = latest.get(c["case_id"])
        status, why = (Status(m["verdict"]), []) if m else (Status.NOT_RUN, [])
        if m:
            pinned = {**(m["pins"] or {}), "build_sha256": m["build_sha256"]}
            status, why = rules.age(
                status, pinned, {**(current_inputs or {}), "build_sha256": build_sha256}
            )
        if status is Status.EXPIRED:
            expired.append(c["case_id"])
        snapshot[c["case_id"]] = {
            "suite": c["suite"], "risk": c["risk"], "status": status.value, "manifest_id": str(m["manifest_id"]) if m else None, "expired_inputs": why,
        }  # fmt: skip
    all_facts = dict(facts)
    pol = await policy_for(
        conn, release_unit
    )  # the policy is read from the signed record, never taken from the caller
    all_facts["thresholds_policy_signed"] = bool(pol and pol["signed"])
    all_facts["thresholds_policy_id"] = pol["policy_id"] if pol else None
    if privacy_gate:
        g = await release_gate(
            conn, privacy_gate["market"], privacy_gate["product"], privacy_gate["version"]
        )
        all_facts["privacy_release_ready"] = bool(g.get("ready"))
        all_facts["privacy_blockers"] = list(g.get("blockers", []))
    else:
        all_facts["privacy_release_ready"] = None
    release_id = await store.insert_candidate(
        conn, tenant,
        {
            "baseline_id": str(base["baseline_id"]), "release_unit": dict(release_unit), "build_sha256": build_sha256, "policy_pack": policy_pack,
            "snapshot": {"cases": snapshot}, "facts": all_facts, "engineering_owner": engineering_owner, "commit_authors": list(commit_authors),
            "created_by": actor,
        },
    )  # fmt: skip
    for cid in expired:
        await _announce(
            conn,
            tenant,
            "assurance.case.expired",
            "uat_case",
            f"{release_id}:{cid}",
            {
                "release_id": release_id,
                "case_id": cid,
                "reason": "PINNED_INPUT_CHANGED",
                "inputs": snapshot[cid]["expired_inputs"],
            },
        )
    return {
        "release_id": release_id,
        "status": "OPEN",
        "expired_cases": expired,
        **await preview(conn, release_id),
    }


async def preview(conn: AsyncConnection, release_id: str) -> dict[str, Any]:
    row = await store.get_candidate(conn, release_id)
    if row is None:
        raise RuleError("RELEASE_NOT_FOUND")
    sigs = await store.signatures_of(conn, release_id)
    d = rules.evaluate(_candidate(row, sigs))
    return {
        "release_id": release_id,
        "candidate_status": row["status"],
        "locked_reason": row["locked_reason"],
        "decision": _decision_view(d),
        "signatures": [{"role": s["role"], "decision": s["decision"]} for s in sigs],
    }


async def sign_decision(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    release_id: str,
    role: str,
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    row = await store.get_candidate(conn, release_id, lock=True)
    if row is None:
        raise RuleError("RELEASE_NOT_FOUND")
    if row["status"] in ("LOCKED", "ROLLED_BACK"):
        raise RuleError("RELEASE_NOT_OPEN_FOR_DECISIONS", row["status"])
    if role not in rules.RELEASE_ROLES:
        raise RuleError("ROLE_CANNOT_SIGN_A_RELEASE", role)
    if role not in await store.roles_of(conn, actor):
        raise RuleError("ROLE_NOT_ASSIGNED_TO_THE_CALLER", role)
    if not rationale.strip():
        raise RuleError("RATIONALE_REQUIRED")
    sigs = await store.signatures_of(conn, release_id)
    before = set(rules.signature_problems(_candidate(row, sigs)))
    after_sigs = [*sigs, {"role": role, "principal": actor, "decision": decision}]
    new = sorted(set(rules.signature_problems(_candidate(row, after_sigs))) - before)
    if new:
        raise RuleError("SIGNATURE_REFUSED", ", ".join(new))
    jws = sign_jws(
        current_runtime().signer,
        {
            "release_id": release_id,
            "role": role,
            "principal": actor,
            "decision": decision,
            "rationale_sha256": _sha(rationale),
            "build_sha256": row["build_sha256"],
            "at": _now().isoformat(),
        },
        typ=DECISION_TYP,
    )
    await store.insert_signature(
        conn,
        tenant,
        release_id=release_id,
        role=role,
        principal=actor,
        decision=decision,
        rationale=rationale,
        jws=jws,
    )
    d = rules.evaluate(_candidate(row, await store.signatures_of(conn, release_id)))
    status = "GO" if d.status == "GO" else "NO_GO"
    await store.set_candidate_status(conn, release_id, status)
    await _announce(
        conn,
        tenant,
        "assurance.release.decided",
        "uat_release",
        release_id,
        {"release_id": release_id, "role": role, "decision": decision, "status": status},
    )
    return {
        "release_id": release_id,
        "candidate_status": status,
        "decision": _decision_view(d),
        "jws": jws,
    }


async def rollback(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    release_id: str,
    scope: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    row = await store.get_candidate(conn, release_id, lock=True)
    if row is None:
        raise RuleError("RELEASE_NOT_FOUND")
    if not (await store.roles_of(conn, actor)) & set(rules.RELEASE_ROLES):
        raise RuleError(
            "ROLLBACK_NEEDS_A_RELEASE_ROLE", "only a release role holder can disable a scope"
        )
    if row["status"] == "ROLLED_BACK":
        raise RuleError("ALREADY_ROLLED_BACK")
    if not reason.strip():
        raise RuleError("REASON_REQUIRED")
    ident = await store.insert_rollback(
        conn, tenant, release_id=release_id, scope=scope, reason=reason, by=actor
    )
    await store.set_candidate_status(conn, release_id, "ROLLED_BACK")
    closed = None
    if scope.get("provider_id") and scope.get(
        "service_code"
    ):  # the rollback closes the exact provider route it names, in the same transaction
        try:
            await change_entitlement_status(
                conn,
                tenant,
                str(scope["provider_id"]),
                str(scope["service_code"]),
                "SUSPENDED",
                "assurance rollback",
            )
            closed = True
        except EntitlementNotFound:
            closed = False
    await _announce(
        conn,
        tenant,
        "assurance.release.rolled_back",
        "uat_release",
        release_id,
        {"release_id": release_id, "rollback_id": ident, "scope": dict(scope)},
    )
    return {
        "release_id": release_id,
        "rollback_id": ident,
        "candidate_status": "ROLLED_BACK",
        "scope": dict(scope),
        "evidence_kept": True,
        "provider_route_closed": closed,
    }


def new_id() -> str:
    return str(uuid.uuid4())


async def confirm_risks(
    conn: AsyncConnection, tenant: str, actor: str, *, risks: Mapping[str, str]
) -> dict[str, Any]:
    """The QA lead confirms case risks (P0, P1 or P2). A new baseline carries them; the earlier one is untouched. Nobody else can confirm, and a case
    that is not in the baseline cannot be given a risk."""
    if "QA_LEAD" not in await store.roles_of(conn, actor):
        raise RuleError("ROLE_NOT_ASSIGNED_TO_THE_CALLER", "only the QA lead confirms risk")
    base = await store.latest_baseline(conn)
    if base is None:
        raise RuleError("NO_BASELINE")
    known = {c["case_id"] for c in await store.baseline_cases(conn, str(base["baseline_id"]))}
    unknown = sorted(set(risks) - known)
    if unknown or not risks or any(v not in ("P0", "P1", "P2") for v in risks.values()):
        raise RuleError(
            "RISK_CONFIRMATION_INVALID", ", ".join(unknown) or "each risk must be P0, P1 or P2"
        )
    versions = await store.baseline_versions(conn)
    version = f"{str(base['version']).split('.r')[0]}.r{sum(1 for v in versions if v['risk_confirmed_by']) + 1}"
    digest = _sha(rules.canonical(sorted(risks.items())) + str(base["source_sha256"]))
    new_id = await store.confirm_baseline_risk(
        conn,
        tenant,
        old_baseline_id=str(base["baseline_id"]),
        version=version,
        source_sha256=digest,
        risks=risks,
        by=actor,
    )
    return {
        "baseline_id": new_id,
        "version": version,
        "confirmed": len(risks),
        "confirmed_by": actor,
    }


async def watch_registry(
    conn: AsyncConnection,
    tenant: str,
    *,
    previous: Sequence[Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
    today: Any,
) -> dict[str, Any]:
    """Compare two snapshots of an official register and close the exact provider routes that drifted. Nothing is fetched: a person or a job supplies the
    snapshots. A removed, expired or withdrawn service is REVOKED; a suspended one is SUSPENDED. Dependent cases lose their PASS at the next candidate."""
    from zid_platform.modules.assurance.domain import provider_watch as pw

    def snap(items: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], pw.Service]:
        out = {}
        for i in items:
            one = pw.Service(
                str(i["provider_id"]),
                str(i["service_code"]),
                str(i["status"]),
                i.get("valid_until"),
                i.get("scheme_version"),
            )
            out[one.key] = one
        return out

    before, now = snap(previous), snap(current)
    changes = pw.diff(before, now, today=today)
    applied = []
    for pid, code, kind in pw.disable_actions(changes, now):
        status = (
            "SUSPENDED"
            if now.get((pid, code)) and now[(pid, code)].status == "SUSPENDED"
            else "REVOKED"
        )
        try:
            await change_entitlement_status(
                conn, tenant, pid, code, status, f"registry drift: {kind}"
            )
            outcome = status
        except EntitlementNotFound:
            outcome = "NO_LIVE_ENTITLEMENT"
        applied.append(
            {"provider_id": pid, "service_code": code, "change": kind, "result": outcome}
        )
    return {
        "changes": [
            {
                "kind": c.kind,
                "provider_id": c.provider_id,
                "service_code": c.service_code,
                "detail": c.detail,
            }
            for c in changes
        ],
        "applied": applied,
        "pins_to_expire": pw.pins_to_expire(changes),
        "note": "Nothing was fetched. Cases pinned to a changed service lose their PASS when a release candidate is next built.",
    }


async def list_fixtures(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [
        {**f, "fixture_id": str(f["fixture_id"]), "author": str(f["author"]), "reviewer": str(f["reviewer"]) if f["reviewer"] else None, "created_at": f["created_at"].isoformat()}
        for f in await store.list_fixtures(conn)
    ]  # fmt: skip


async def list_runs(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [
        {
            "run_id": str(r["run_id"]), "environment_id": r["environment_id"], "build_sha256": r["build_sha256"], "release_unit": r["release_unit"],
            "tester_principal": str(r["tester_principal"]), "kind": r["kind"], "created_at": r["created_at"].isoformat(), "events": int(r["events"]), "manifests": int(r["manifests"]),
        }
        for r in await store.list_runs(conn)
    ]  # fmt: skip


POLICY_TYP = "zid-uat-policy+jwt"


async def propose_policy(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    release_unit: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """A signed SLO and assurance policy starts as a proposal by the QA lead or the CTO. The numbers are theirs: this code supplies none."""
    if not (await store.roles_of(conn, actor)) & {"QA_LEAD", "CTO"}:
        raise RuleError(
            "ROLE_NOT_ASSIGNED_TO_THE_CALLER", "the QA lead or the CTO proposes a policy"
        )
    problems = rules.policy_problems(thresholds)
    if problems:
        raise RuleError("POLICY_INVALID", ", ".join(problems))
    ident = await store.insert_policy(
        conn, tenant, release_unit=release_unit, thresholds=thresholds, by=actor
    )
    return {"policy_id": ident, "signed": False, "needs": list(rules.POLICY_ROLES)}


async def sign_policy(
    conn: AsyncConnection,
    tenant: str,
    actor: str,
    *,
    policy_id: str,
    role: str,
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    pol = await store.get_policy(conn, policy_id)
    if pol is None:
        raise RuleError("POLICY_NOT_FOUND")
    if role not in rules.POLICY_ROLES or role not in await store.roles_of(conn, actor):
        raise RuleError("ROLE_NOT_ASSIGNED_TO_THE_CALLER", role)
    if not rationale.strip():
        raise RuleError("RATIONALE_REQUIRED")
    existing = await store.policy_signatures(conn, policy_id)
    if any(s["role"] == role for s in existing) or any(
        str(s["principal"]) == actor for s in existing
    ):
        raise RuleError(
            "SIGNATURE_REFUSED", "each role signs once, and one person signs for one role"
        )
    jws = sign_jws(
        current_runtime().signer,
        {
            "policy_id": policy_id,
            "role": role,
            "principal": actor,
            "decision": decision,
            "thresholds_sha256": _sha(rules.canonical(pol["thresholds"])),
            "at": _now().isoformat(),
        },
        typ=POLICY_TYP,
    )
    await store.insert_policy_signature(
        conn,
        tenant,
        policy_id=policy_id,
        role=role,
        principal=actor,
        decision=decision,
        rationale=rationale,
        jws=jws,
    )
    return await policy_view(conn, policy_id)


async def policy_view(conn: AsyncConnection, policy_id: str) -> dict[str, Any]:
    pol = await store.get_policy(conn, policy_id)
    if pol is None:
        raise RuleError("POLICY_NOT_FOUND")
    sigs = await store.policy_signatures(conn, policy_id)
    return {
        "policy_id": policy_id, "release_unit": pol["release_unit"], "thresholds": pol["thresholds"], "signed": rules.policy_signed(sigs),
        "signatures": [{"role": s["role"], "decision": s["decision"]} for s in sigs],
        "needs": [r for r in rules.POLICY_ROLES if r not in {s["role"] for s in sigs if s["decision"] == "APPROVE"}],
    }  # fmt: skip


async def policy_for(
    conn: AsyncConnection, release_unit: Mapping[str, Any]
) -> dict[str, Any] | None:
    pol = await store.latest_policy(conn, release_unit)
    return await policy_view(conn, str(pol["policy_id"])) if pol else None


async def check_measurements(
    conn: AsyncConnection, release_unit: Mapping[str, Any], measured: Mapping[str, Any]
) -> dict[str, Any]:
    """Judge measurements against the release unit's SIGNED policy. With no signed policy there is nothing to judge against: that is refused, not defaulted."""
    pol = await policy_for(conn, release_unit)
    if pol is None or not pol["signed"]:
        raise RuleError(
            "POLICY_NOT_SIGNED",
            "no signed policy covers this release unit, so no number can be judged",
        )
    return {
        "policy_id": pol["policy_id"],
        "breaches": rules.check_thresholds(pol["thresholds"], measured),
    }


def draft_proposal() -> dict[str, Any]:
    """The numbers a controlled document already proposes (and which it came from), and the ones nobody has proposed. Read from config; never defaulted in code."""
    import os
    from pathlib import Path

    import yaml

    base = Path(
        os.environ.get("ZID_ASSURANCE_CONFIG")
        or Path(__file__).resolve().parents[6] / "config" / "assurance"
    )
    path = base / "policy-proposal.yaml"
    doc = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    return {
        "thresholds": dict(doc.get("thresholds") or {}),
        "no_number_proposed": list(doc.get("no_number_proposed") or []),
    }


async def propose_policy_from_draft(
    conn: AsyncConnection, tenant: str, actor: str, *, release_unit: Mapping[str, Any]
) -> dict[str, Any]:
    """An UNSIGNED proposal carrying only the documents' proposed numbers. It changes nothing until three role holders sign it, and it leaves every number
    no document proposes absent, for the owners to add by proposing a policy of their own."""
    draft = draft_proposal()
    if not draft["thresholds"]:
        raise RuleError(
            "NO_DRAFT_PROPOSAL", "config/assurance/policy-proposal.yaml holds no numbers"
        )
    out = await propose_policy(
        conn, tenant, actor, release_unit=release_unit, thresholds=draft["thresholds"]
    )
    return {
        **out,
        "from_draft": True,
        "thresholds": draft["thresholds"],
        "no_number_proposed": draft["no_number_proposed"],
    }
