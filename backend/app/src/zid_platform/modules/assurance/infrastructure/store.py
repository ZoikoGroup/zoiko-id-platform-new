"""Storage for the assurance plane (evidence.uat_* tables). SQL only: the rules are in `domain/rules.py` and the use cases in `application/service.py`.

Everything runs inside the caller's tenant transaction, so row-level security keeps one operating entity's runs away from another's. Rows hold ids,
digests, statuses and references; never a biometric, a document or a claim value.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

U = uuid.UUID


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def _one(conn: AsyncConnection, sql: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
    r = (await conn.execute(text(sql), dict(params))).first()
    return _row(r) if r else None


async def _all(
    conn: AsyncConnection, sql: str, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [_row(r) for r in (await conn.execute(text(sql), dict(params or {}))).all()]


# ---------------------------------------------------------------------------------------------------------------------------- baselines


async def insert_baseline(
    conn: AsyncConnection,
    tenant: str,
    *,
    version: str,
    source_sha256: str,
    cases: Sequence[Mapping[str, Any]],
    by: str,
) -> str:
    baseline_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_baseline (baseline_id, tenant_id, version, source_sha256, case_count, created_by) "
            "VALUES (:b, :t, :v, :s, :n, :by)"
        ),
        {
            "b": U(baseline_id),
            "t": U(tenant),
            "v": version,
            "s": source_sha256,
            "n": len(cases),
            "by": U(by),
        },
    )
    for c in cases:
        await conn.execute(
            text(
                "INSERT INTO evidence.uat_case (baseline_id, tenant_id, case_id, suite, risk, dependency, assertion, oracle_version) "
                "VALUES (:b, :t, :c, :s, :r, :d, :a, :o)"
            ),
            {
                "b": U(baseline_id), "t": U(tenant), "c": c["case_id"], "s": c["suite"], "r": c["risk"], "d": c["dependency"],
                "a": c["assertion"], "o": c.get("oracle_version", "v1"),
            },
        )  # fmt: skip
    return baseline_id


async def confirm_baseline_risk(
    conn: AsyncConnection,
    tenant: str,
    *,
    old_baseline_id: str,
    version: str,
    source_sha256: str,
    risks: Mapping[str, str],
    by: str,
) -> str:
    """A new baseline with the same cases and the confirmed risks. The earlier baseline is never edited, so results keep what they were judged against."""
    new_id = str(uuid.uuid4())
    old = await baseline_cases(conn, old_baseline_id)
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_baseline (baseline_id, tenant_id, version, source_sha256, case_count, risk_confirmed_by, created_by) "
            "VALUES (:b, :t, :v, :s, :n, :by, :by)"
        ),
        {
            "b": U(new_id),
            "t": U(tenant),
            "v": version,
            "s": source_sha256,
            "n": len(old),
            "by": U(by),
        },
    )
    for c in old:
        await conn.execute(
            text(
                "INSERT INTO evidence.uat_case (baseline_id, tenant_id, case_id, suite, risk, dependency, assertion, oracle_version) "
                "VALUES (:b, :t, :c, :s, :r, :d, :a, :o)"
            ),
            {"b": U(new_id), "t": U(tenant), "c": c["case_id"], "s": c["suite"], "r": risks.get(c["case_id"], c["risk"]), "d": c["dependency"], "a": c["assertion"], "o": c["oracle_version"]},
        )  # fmt: skip
    return new_id


async def baseline_versions(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT baseline_id, version, case_count, risk_confirmed_by, created_at FROM evidence.uat_baseline ORDER BY created_at",
    )


async def latest_baseline(conn: AsyncConnection) -> dict[str, Any] | None:
    return await _one(
        conn, "SELECT * FROM evidence.uat_baseline ORDER BY created_at DESC LIMIT 1", {}
    )


async def baseline_cases(conn: AsyncConnection, baseline_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT case_id, suite, risk, dependency, assertion, oracle_version FROM evidence.uat_case WHERE baseline_id = :b ORDER BY case_id",
        {"b": U(baseline_id)},
    )


# ---------------------------------------------------------------------------------------------------------------------------------- roles


async def assign_role(
    conn: AsyncConnection, tenant: str, *, role: str, principal: str, by: str
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_role_assignment (assignment_id, tenant_id, role, principal, assigned_by) VALUES (:i, :t, :r, :p, :by)"
        ),
        {"i": U(ident), "t": U(tenant), "r": role, "p": U(principal), "by": U(by)},
    )
    return ident


async def roles_of(conn: AsyncConnection, principal: str) -> set[str]:
    rows = await _all(
        conn,
        "SELECT role FROM evidence.uat_role_assignment WHERE principal = :p AND active",
        {"p": U(principal)},
    )
    return {r["role"] for r in rows}


async def withdraw_role(conn: AsyncConnection, *, role: str, principal: str) -> int:
    res = await conn.execute(
        text(
            "UPDATE evidence.uat_role_assignment SET active = false WHERE role = :r AND principal = :p AND active"
        ),
        {"r": role, "p": U(principal)},
    )
    return int(res.rowcount or 0)


# ------------------------------------------------------------------------------------------------------------------------------ fixtures


async def insert_fixture(
    conn: AsyncConnection, tenant: str, *, name: str, content_sha256: str, author: str
) -> dict[str, str]:
    fixture_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_fixture (fixture_id, tenant_id, name, created_by) VALUES (:f, :t, :n, :by)"
        ),
        {"f": U(fixture_id), "t": U(tenant), "n": name, "by": U(author)},
    )
    await add_revision(
        conn,
        tenant,
        fixture_id=fixture_id,
        revision=1,
        content_sha256=content_sha256,
        author=author,
        revision_id=revision_id,
    )
    return {"fixture_id": fixture_id, "revision_id": revision_id}


async def add_revision(
    conn: AsyncConnection,
    tenant: str,
    *,
    fixture_id: str,
    revision: int,
    content_sha256: str,
    author: str,
    revision_id: str | None = None,
) -> str:
    ident = revision_id or str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_fixture_revision (revision_id, tenant_id, fixture_id, revision, content_sha256, author, state) "
            "VALUES (:i, :t, :f, :r, :h, :a, 'DRAFT')"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "f": U(fixture_id),
            "r": revision,
            "h": content_sha256,
            "a": U(author),
        },
    )
    return ident


async def fixture_revisions(conn: AsyncConnection, fixture_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT * FROM evidence.uat_fixture_revision WHERE fixture_id = :f ORDER BY revision",
        {"f": U(fixture_id)},
    )


async def review_revision(
    conn: AsyncConnection, revision_id: str, *, state: str, reviewer: str, rationale: str
) -> None:
    await conn.execute(
        text(
            "UPDATE evidence.uat_fixture_revision SET state = :s, reviewer = :r, rationale = :ra, reviewed_at = now() "
            "WHERE revision_id = :i"
        ),
        {"s": state, "r": U(reviewer), "ra": rationale, "i": U(revision_id)},
    )


async def approved_hashes(conn: AsyncConnection) -> set[str]:
    rows = await _all(
        conn, "SELECT content_sha256 FROM evidence.uat_fixture_revision WHERE state = 'APPROVED'"
    )
    return {r["content_sha256"] for r in rows}


# ---------------------------------------------------------------------------------------------------------------------------------- runs


async def insert_run(conn: AsyncConnection, tenant: str, run: Mapping[str, Any]) -> str:
    run_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_run (run_id, tenant_id, baseline_id, release_unit, environment_id, build_sha256, config_digest, "
            "test_set_sha256, fixture_hashes, pins, tester_principal, tester_role, kind) VALUES (:i, :t, :b, CAST(:ru AS jsonb), :e, :bs, :cd, :ts, :fh, CAST(:pn AS jsonb), :tp, :tr, :kd)"
        ),
        {
            "i": U(run_id), "t": U(tenant), "b": U(run["baseline_id"]), "ru": json.dumps(run["release_unit"], sort_keys=True),
            "e": run["environment_id"], "bs": run["build_sha256"], "cd": run["config_digest"], "ts": run["test_set_sha256"],
            "fh": list(run["fixture_hashes"]), "pn": json.dumps(run.get("pins", {}), sort_keys=True), "tp": U(run["tester_principal"]), "tr": run["tester_role"], "kd": run.get("kind", "INDEPENDENT"),
        },
    )  # fmt: skip
    return run_id


async def get_run(conn: AsyncConnection, run_id: str) -> dict[str, Any] | None:
    return await _one(conn, "SELECT * FROM evidence.uat_run WHERE run_id = :i", {"i": U(run_id)})


async def last_event(conn: AsyncConnection, run_id: str) -> dict[str, Any] | None:
    return await _one(
        conn,
        "SELECT seq, hash FROM evidence.uat_event WHERE run_id = :r ORDER BY seq DESC LIMIT 1 FOR UPDATE",
        {"r": U(run_id)},
    )


async def event_by_key(conn: AsyncConnection, run_id: str, idem_key: str) -> dict[str, Any] | None:
    return await _one(
        conn,
        "SELECT * FROM evidence.uat_event WHERE run_id = :r AND idem_key = :k",
        {"r": U(run_id), "k": idem_key},
    )


async def insert_event(
    conn: AsyncConnection, tenant: str, *, run_id: str, seq: int, case_id: str | None, kind: str, payload: Mapping[str, Any],
    prev_hash: str, hash_: str, idem_key: str,
) -> str:  # fmt: skip
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_event (event_id, tenant_id, run_id, seq, case_id, kind, payload, prev_hash, hash, idem_key) "
            "VALUES (:i, :t, :r, :s, :c, :k, CAST(:p AS jsonb), :ph, :h, :ik)"
        ),
        {
            "i": U(ident), "t": U(tenant), "r": U(run_id), "s": seq, "c": case_id, "k": kind, "p": json.dumps(payload, sort_keys=True),
            "ph": prev_hash, "h": hash_, "ik": idem_key,
        },
    )  # fmt: skip
    return ident


async def events_of(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn, "SELECT * FROM evidence.uat_event WHERE run_id = :r ORDER BY seq", {"r": U(run_id)}
    )


# ------------------------------------------------------------------------------------------------------------------------------ manifests


async def insert_manifest(
    conn: AsyncConnection, tenant: str, *, run_id: str, case_id: str, claimed: str, verdict: str, manifest: Mapping[str, Any],
    manifest_sha256: str, problems: Sequence[str], jws: str, kid: str, chain_head: str,
) -> str:  # fmt: skip
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_manifest (manifest_id, tenant_id, run_id, case_id, claimed_result, verdict, manifest, manifest_sha256, "
            "problems, jws, signed_kid, chain_head) VALUES (:i, :t, :r, :c, :cl, :v, CAST(:m AS jsonb), :h, :p, :j, :k, :ch)"
        ),
        {
            "i": U(ident), "t": U(tenant), "r": U(run_id), "c": case_id, "cl": claimed, "v": verdict,
            "m": json.dumps(manifest, sort_keys=True), "h": manifest_sha256, "p": list(problems), "j": jws, "k": kid, "ch": chain_head,
        },
    )  # fmt: skip
    return ident


async def get_manifest(conn: AsyncConnection, manifest_id: str) -> dict[str, Any] | None:
    return await _one(
        conn, "SELECT * FROM evidence.uat_manifest WHERE manifest_id = :i", {"i": U(manifest_id)}
    )


async def manifests_of_run(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT * FROM evidence.uat_manifest WHERE run_id = :r ORDER BY created_at",
        {"r": U(run_id)},
    )


async def latest_manifests(
    conn: AsyncConnection, release_unit: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """The newest manifest per case among runs for exactly this release unit, with the inputs its run was pinned to."""
    rows = await _all(
        conn,
        "SELECT DISTINCT ON (m.case_id) m.case_id, m.manifest_id, m.verdict, m.created_at, r.run_id, r.build_sha256, r.environment_id, r.fixture_hashes, "
        "r.pins, r.release_unit FROM evidence.uat_manifest m JOIN evidence.uat_run r ON r.run_id = m.run_id "
        "WHERE r.release_unit = CAST(:ru AS jsonb) AND r.kind = 'INDEPENDENT' ORDER BY m.case_id, m.created_at DESC",
        {"ru": json.dumps(release_unit, sort_keys=True)},
    )
    return {r["case_id"]: r for r in rows}


async def runs_for_manifest(conn: AsyncConnection, manifest_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT c.release_id FROM evidence.uat_release_candidate c WHERE c.snapshot::text LIKE :like",
        {"like": f"%{manifest_id}%"},
    )


# --------------------------------------------------------------------------------------------------------------------------------- defects


async def insert_defect(
    conn: AsyncConnection, tenant: str, *, case_id: str, severity: str, owner: str, by: str
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_defect (defect_id, tenant_id, case_id, severity, owner, status, created_by) VALUES (:i, :t, :c, :s, :o, 'OPEN', :by)"
        ),
        {"i": U(ident), "t": U(tenant), "c": case_id, "s": severity, "o": owner, "by": U(by)},
    )
    return ident


async def get_defect(conn: AsyncConnection, defect_id: str) -> dict[str, Any] | None:
    return await _one(
        conn, "SELECT * FROM evidence.uat_defect WHERE defect_id = :i", {"i": U(defect_id)}
    )


async def open_defects(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await _all(
        conn, "SELECT * FROM evidence.uat_defect WHERE status = 'OPEN' ORDER BY created_at"
    )


# --------------------------------------------------------------------------------------------------------------------- release candidates


async def insert_candidate(conn: AsyncConnection, tenant: str, c: Mapping[str, Any]) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_release_candidate (release_id, tenant_id, baseline_id, release_unit, build_sha256, policy_pack, snapshot, facts, "
            "engineering_owner, commit_authors, status, created_by) VALUES (:i, :t, :b, CAST(:ru AS jsonb), :bs, :pp, CAST(:sn AS jsonb), "
            "CAST(:f AS jsonb), :eo, :ca, 'OPEN', :by)"
        ),
        {
            "i": U(ident), "t": U(tenant), "b": U(c["baseline_id"]), "ru": json.dumps(c["release_unit"], sort_keys=True), "bs": c["build_sha256"],
            "pp": c["policy_pack"], "sn": json.dumps(c["snapshot"], sort_keys=True), "f": json.dumps(c["facts"], sort_keys=True),
            "eo": U(c["engineering_owner"]) if c.get("engineering_owner") else None, "ca": list(c["commit_authors"]), "by": U(c["created_by"]),
        },
    )  # fmt: skip
    return ident


async def get_candidate(
    conn: AsyncConnection, release_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    return await _one(
        conn,
        "SELECT * FROM evidence.uat_release_candidate WHERE release_id = :i"
        + (" FOR UPDATE" if lock else ""),
        {"i": U(release_id)},
    )


async def set_candidate_status(
    conn: AsyncConnection, release_id: str, status: str, reason: str | None = None
) -> None:
    await conn.execute(
        text(
            "UPDATE evidence.uat_release_candidate SET status = :s, locked_reason = coalesce(:r, locked_reason) WHERE release_id = :i"
        ),
        {"s": status, "r": reason, "i": U(release_id)},
    )


async def insert_signature(
    conn: AsyncConnection,
    tenant: str,
    *,
    release_id: str,
    role: str,
    principal: str,
    decision: str,
    rationale: str,
    jws: str,
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_release_signature (signature_id, tenant_id, release_id, role, principal, decision, rationale, jws) "
            "VALUES (:i, :t, :r, :ro, :p, :d, :ra, :j)"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "r": U(release_id),
            "ro": role,
            "p": U(principal),
            "d": decision,
            "ra": rationale,
            "j": jws,
        },
    )
    return ident


async def signatures_of(conn: AsyncConnection, release_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT role, principal, decision, rationale, created_at FROM evidence.uat_release_signature WHERE release_id = :r ORDER BY created_at",
        {"r": U(release_id)},
    )


async def insert_rollback(
    conn: AsyncConnection,
    tenant: str,
    *,
    release_id: str,
    scope: Mapping[str, Any],
    reason: str,
    by: str,
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_rollback (rollback_id, tenant_id, release_id, scope, reason, by_principal) VALUES (:i, :t, :r, CAST(:s AS jsonb), :re, :by)"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "r": U(release_id),
            "s": json.dumps(scope, sort_keys=True),
            "re": reason,
            "by": U(by),
        },
    )
    return ident


async def next_event_version(conn: AsyncConnection, aggregate_type: str, aggregate_id: str) -> int:
    row = await conn.execute(
        text(
            "SELECT coalesce(max(aggregate_version), 0) + 1 FROM events.outbox_event WHERE aggregate_type = :t AND aggregate_id = :i"
        ),
        {"t": aggregate_type, "i": aggregate_id},
    )
    return int(row.scalar_one())


async def latest_any(conn: AsyncConnection, kind: str = "INDEPENDENT") -> dict[str, dict[str, Any]]:
    """The newest manifest per case across every run of one kind. Only INDEPENDENT runs count toward a verdict; DEVELOPER runs are shown beside them."""
    rows = await _all(
        conn,
        "SELECT DISTINCT ON (m.case_id) m.case_id, m.manifest_id, m.verdict, m.created_at FROM evidence.uat_manifest m "
        "JOIN evidence.uat_run r ON r.run_id = m.run_id WHERE r.kind = :k ORDER BY m.case_id, m.created_at DESC",
        {"k": kind},
    )
    return {r["case_id"]: r for r in rows}


async def list_fixtures(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT f.fixture_id, f.name, f.created_at, r.revision, r.state, r.content_sha256, r.author, r.reviewer FROM evidence.uat_fixture f "
        "JOIN LATERAL (SELECT * FROM evidence.uat_fixture_revision x WHERE x.fixture_id = f.fixture_id ORDER BY x.revision DESC LIMIT 1) r ON true "
        "ORDER BY f.created_at DESC LIMIT 200",
    )


async def list_runs(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT r.run_id, r.environment_id, r.build_sha256, r.release_unit, r.tester_principal, r.kind, r.created_at, "
        "(SELECT count(*) FROM evidence.uat_event e WHERE e.run_id = r.run_id) AS events, "
        "(SELECT count(*) FROM evidence.uat_manifest m WHERE m.run_id = r.run_id) AS manifests "
        "FROM evidence.uat_run r ORDER BY r.created_at DESC LIMIT 100",
    )


async def insert_policy(
    conn: AsyncConnection,
    tenant: str,
    *,
    release_unit: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    by: str,
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_policy (policy_id, tenant_id, release_unit, thresholds, proposed_by) VALUES (:i, :t, CAST(:ru AS jsonb), CAST(:th AS jsonb), :by)"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "ru": json.dumps(release_unit, sort_keys=True),
            "th": json.dumps(thresholds, sort_keys=True),
            "by": U(by),
        },
    )
    return ident


async def get_policy(conn: AsyncConnection, policy_id: str) -> dict[str, Any] | None:
    return await _one(
        conn, "SELECT * FROM evidence.uat_policy WHERE policy_id = :i", {"i": U(policy_id)}
    )


async def latest_policy(
    conn: AsyncConnection, release_unit: Mapping[str, Any]
) -> dict[str, Any] | None:
    return await _one(
        conn,
        "SELECT * FROM evidence.uat_policy WHERE release_unit = CAST(:ru AS jsonb) ORDER BY created_at DESC LIMIT 1",
        {"ru": json.dumps(release_unit, sort_keys=True)},
    )


async def insert_policy_signature(
    conn: AsyncConnection,
    tenant: str,
    *,
    policy_id: str,
    role: str,
    principal: str,
    decision: str,
    rationale: str,
    jws: str,
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_policy_signature (signature_id, tenant_id, policy_id, role, principal, decision, rationale, jws) "
            "VALUES (:i, :t, :p, :ro, :pr, :d, :ra, :j)"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "p": U(policy_id),
            "ro": role,
            "pr": U(principal),
            "d": decision,
            "ra": rationale,
            "j": jws,
        },
    )
    return ident


async def policy_signatures(conn: AsyncConnection, policy_id: str) -> list[dict[str, Any]]:
    return await _all(
        conn,
        "SELECT role, principal, decision FROM evidence.uat_policy_signature WHERE policy_id = :p ORDER BY created_at",
        {"p": U(policy_id)},
    )


async def insert_snapshot(
    conn: AsyncConnection,
    tenant: str,
    *,
    source: str,
    simulated: bool,
    services: Sequence[Mapping[str, Any]],
    sha256: str,
) -> str:
    ident = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO evidence.uat_registry_snapshot (snapshot_id, tenant_id, source, simulated, services, sha256) VALUES (:i, :t, :s, :sim, CAST(:sv AS jsonb), :h)"
        ),
        {
            "i": U(ident),
            "t": U(tenant),
            "s": source,
            "sim": simulated,
            "sv": json.dumps(list(services), sort_keys=True, default=str),
            "h": sha256,
        },
    )
    return ident


async def last_snapshot(conn: AsyncConnection, source: str) -> dict[str, Any] | None:
    return await _one(
        conn,
        "SELECT * FROM evidence.uat_registry_snapshot WHERE source = :s ORDER BY taken_at DESC LIMIT 1",
        {"s": source},
    )
