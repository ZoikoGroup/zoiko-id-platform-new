"""CR-142: the access readiness agent runs for an agent bound to an API client, with no container and no attestor, and only while that binding holds.

An API client proves a key, not what code runs (CR-093), so its proof is capped at level 1 and it is never mistaken for a verified image. A run asks the
same question the token path asks of an API agent: agent active, not frozen, binding live and in date, client build approved and not revoked, release
unchanged. Every lapse is seen at the very next run and, for a lease already issued, at the very next step. Synthetic records only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from access_readiness_fixture import DIGEST, World
from test_access_readiness_pg import approved_world, execute, make_run
from zid_platform.modules.machine.application import leases
from zid_platform.modules.machine.application.identity_status import agent_identity_status
from zid_platform.modules.machine.domain.api_agents import API_ATTESTATION_CAP

OK = ("NEEDS_REVIEW", "COMPLETED")


def runs_state(w: World, run_id: str) -> tuple[str, list[str], str | None]:
    row = w.owner(
        "SELECT state, denial_codes, worker_attestation_id FROM agent.access_review_run WHERE tenant_id = :t AND run_id = :r",
        r=uuid.UUID(run_id),
    )[0]
    return row[0], list(row[1] or []), (str(row[2]) if row[2] else None)


def drop_container_proof(w: World) -> None:
    """The agent stops being a container agent: its workload binding ends."""
    w.owner(
        "UPDATE agent.workload_binding SET status = 'REVOKED' WHERE tenant_id = :t AND logical_principal_id = :a",
        a=uuid.UUID(w.daara),
    )


def bind_api_client(
    w: World, *, valid_for: timedelta = timedelta(days=30), build: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """An approved API client build for the agent's release and a live binding to it: what registering a client, a second person approving it,
    cutting the release from it and binding it leaves behind. A second binding reuses the build (a digest is registered once per company).
    Returns (build id, binding id)."""
    binding = uuid.uuid4()
    if build is None:
        build = uuid.uuid4()
        w.owner(
            "INSERT INTO agent.build_artifact (build_id, tenant_id, name, artifact_digest, registered_by, review_status, approved_by, registered_at, approved_at, "
            "kind, client_descriptor) VALUES (:b, :t, 'proof-client', :d, :rb, 'APPROVED', :ab, now(), now(), 'API_CLIENT', CAST('{}' AS jsonb))",
            b=build, d=DIGEST, rb=uuid.UUID(w.admin), ab=uuid.UUID(w.approver),
        )  # fmt: skip
    w.owner(
        "INSERT INTO agent.api_client_binding (binding_id, tenant_id, agent_principal_id, build_id, client_id, descriptor_digest, status, valid_to, created_by) "
        "VALUES (:i, :t, :a, :b, :c, :d, 'ACTIVE', :v, :by)",
        i=binding, a=uuid.UUID(w.daara), b=build, c=f"proof-{binding.hex[:8]}", d=DIGEST, v=datetime.now(UTC) + valid_for, by=uuid.UUID(w.admin),
    )  # fmt: skip
    return build, binding


def test_an_api_bound_agent_runs_only_while_its_binding_holds(rt_url: str, owner_url: str) -> None:
    w = approved_world(rt_url, owner_url)

    # the container path still works exactly as before, and says what its proof is
    first = execute(w, make_run(w, "idem-container")["run_id"])
    assert first["state"] in OK, first
    status = w.db(lambda c: agent_identity_status(c, w.daara, now=datetime.now(UTC)))
    assert status.proof_kind == "WORKLOAD" and status.attestation_level == 2

    # no proof at all: refused, with the credential step named
    drop_container_proof(w)
    bare = execute(w, make_run(w, "idem-no-proof")["run_id"])
    assert bare["state"] == "DENIED" and "AGENT_NOT_ATTESTED" in bare["denial_codes"], bare

    # a bound API client in date is a proof, held at the level a key can show and not a notch higher
    build, binding = bind_api_client(w)
    status = w.db(lambda c: agent_identity_status(c, w.daara, now=datetime.now(UTC)))
    assert status.proof_kind == "API_CLIENT" and status.attested
    assert status.attestation_level == API_ATTESTATION_CAP == 1
    assert status.attestation_id == str(binding) and status.live_bindings == 1

    ran = execute(w, make_run(w, "idem-api")["run_id"])
    assert ran["state"] in OK, ran
    _state, _codes, attestation = runs_state(w, ran["run_id"])
    assert attestation == str(binding)  # the run records what it rested on
    issued = w.owner(
        "SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'machine.workload.credential_issued' AND target_ref = :b",
        b=str(binding),
    )[0][0]
    assert issued >= 1  # the same evidence a workload credential leaves, naming the API binding

    # revoked: the next run is refused
    w.owner(
        "UPDATE agent.api_client_binding SET status = 'REVOKED', revoked_at = now(), security_revision = security_revision + 1 WHERE binding_id = :i",
        i=binding,
    )
    assert "AGENT_NOT_ATTESTED" in execute(w, make_run(w, "idem-revoked")["run_id"])["denial_codes"]

    # expired: refused
    _b2, expired = bind_api_client(w, build=build)
    w.owner(
        "UPDATE agent.api_client_binding SET valid_to = now() - interval '1 minute' WHERE binding_id = :i",
        i=expired,
    )
    assert "AGENT_NOT_ATTESTED" in execute(w, make_run(w, "idem-expired")["run_id"])["denial_codes"]


def test_the_client_build_being_revoked_or_the_release_changing_ends_the_proof(
    rt_url: str, owner_url: str
) -> None:
    w = approved_world(rt_url, owner_url)
    drop_container_proof(w)
    build, binding = bind_api_client(w)
    assert execute(w, make_run(w, "idem-good")["run_id"])["state"] in OK

    # the build is revoked (CR-064): the agent keeps no proof, and a run is refused at once
    w.owner(
        "UPDATE agent.build_artifact SET review_status = 'REVOKED', revoked_at = now(), revoke_reason = 'COMPROMISED' WHERE build_id = :b",
        b=build,
    )
    refused = execute(w, make_run(w, "idem-build-revoked")["run_id"])
    assert refused["state"] == "DENIED" and "AGENT_NOT_ATTESTED" in refused["denial_codes"]

    # a binding to a different digest than the agent's release is not a proof of this release
    w.owner(
        "UPDATE agent.build_artifact SET review_status = 'APPROVED', revoked_at = NULL, revoke_reason = NULL WHERE build_id = :b",
        b=build,
    )
    assert execute(w, make_run(w, "idem-restored")["run_id"])["state"] in OK
    w.owner(
        "UPDATE agent.api_client_binding SET descriptor_digest = :d WHERE binding_id = :i",
        d="sha256:" + "9" * 64,
        i=binding,
    )
    assert (
        "AGENT_NOT_ATTESTED"
        in execute(w, make_run(w, "idem-other-digest")["run_id"])["denial_codes"]
    )


def test_a_lease_backed_by_an_api_client_is_re_checked_at_every_step(
    rt_url: str, owner_url: str
) -> None:
    """The lease is in process and opaque: an id, an audience, a purpose, a scope and an expiry. It dies when the binding does, at the next step."""
    w = approved_world(rt_url, owner_url)
    drop_container_proof(w)
    _build, binding = bind_api_client(w)
    scope = frozenset({"read_mandate_metadata", "read_agent_identity_status"})

    async def issue(c: Any) -> Any:
        return await leases.issue_workload_lease(
            c, w.tenant, w.daara, audience="aud", purpose="access_posture_review", scope=scope
        )

    lease = w.db(issue)
    assert (
        lease.kind == "API_CLIENT"
        and lease.binding_id == str(binding)
        and lease.environment_id == "api"
    )
    assert lease.workload_identity_id.startswith("api-client:")
    assert (
        lease.expires_at - lease.issued_at <= leases.LEASE_LIFETIME
    )  # no longer than any other lease

    def problems(use: str = "read_mandate_metadata", audience: str = "aud") -> list[str]:
        return w.db(
            lambda c: leases.lease_problems(
                c, lease, audience=audience, purpose="access_posture_review", use=use
            )
        )

    assert problems() == []
    assert "CREDENTIAL_SCOPE" in problems(
        use="create_access_review_proposal"
    )  # a use outside what it was issued for
    assert "CREDENTIAL_AUDIENCE" in problems(audience="elsewhere")

    # the binding is rebound (a new security revision) or revoked: the very next step is refused
    w.owner(
        "UPDATE agent.api_client_binding SET security_revision = security_revision + 1 WHERE binding_id = :i",
        i=binding,
    )
    assert "CREDENTIAL_REVOKED" in problems()
    w.owner(
        "UPDATE agent.api_client_binding SET security_revision = security_revision - 1 WHERE binding_id = :i",
        i=binding,
    )
    assert problems() == []
    w.owner(
        "UPDATE agent.api_client_binding SET status = 'REVOKED', revoked_at = now() WHERE binding_id = :i",
        i=binding,
    )
    assert problems() != []

    # and a revoked binding gets no new lease
    from zid_platform.modules.machine.domain.agents import MachineError

    try:
        w.db(issue)
        raise AssertionError("a lease was issued without a live proof")
    except MachineError as e:
        assert e.code in ("API_BINDING_INACTIVE", "WORKLOAD_IDENTITY_UNREGISTERED")


def test_another_companys_revoked_release_or_quarantine_never_stops_this_companys_agent(
    rt_url: str, owner_url: str
) -> None:
    """The credential decision reads revoked releases and quarantined environments for the binding's own company, named in the query. Two companies can run
    the same image digest and an environment is only a name; the worker's connection is not confined to one company, so a read that leaned on it would
    let company A's revocation or quarantine stop company B's agent."""
    from access_readiness_fixture import runtime
    from conftest import engine
    from zid_platform.modules.access_readiness.application import runs
    from zid_platform.modules.machine.infrastructure import workload_store

    a, b = (
        approved_world(rt_url, owner_url),
        approved_world(rt_url, owner_url),
    )  # the same digest and the same environment name, two companies

    def run_as_worker(w: World, key: str) -> dict[str, Any]:
        run_id = make_run(w, key)["run_id"]

        async def go() -> dict[str, Any]:
            async with (
                engine(owner_url) as eng,
                eng.begin() as conn,
            ):  # no tenant scope, as the worker's connection has none
                return await runs.execute_run(conn, runtime(), w.tenant, run_id)  # type: ignore[no-any-return]

        return w.run(go())  # type: ignore[no-any-return]

    assert run_as_worker(a, "idem-a-before")["state"] in OK
    assert run_as_worker(b, "idem-b-before")["state"] in OK

    # A revokes its release and quarantines its environment
    a.owner("UPDATE agent.blueprint_release SET review_status = 'REVOKED' WHERE tenant_id = :t")
    a.db(lambda c: workload_store.quarantine(c, a.tenant, "prod", "TEST", None))

    refused = run_as_worker(a, "idem-a-after")
    assert refused["state"] == "DENIED" and "CREDENTIAL_DENIED" in refused["denial_codes"], refused
    assert (
        run_as_worker(b, "idem-b-after")["state"] in OK
    )  # B is untouched, though nothing scopes this connection


def test_a_grant_issued_over_the_api_carries_the_audit_record_of_its_issuance(
    rt_url: str, owner_url: str
) -> None:
    """CR-143: the access readiness agent's rule R-05 looks for the policy evidence behind a mandate on its source grant. Nothing ever wrote it, so every mandate in
    a real company read as missing its evidence. A grant now points at the audit record written when it was issued, which is the evidence of who gave whom what."""
    from datetime import UTC, datetime, timedelta

    from access_readiness_fixture import ACTIONS, RESOURCES
    from admin_fixture import make_admin
    from fastapi.testclient import TestClient

    w = World(rt_url, owner_url).start()
    # a grant the admin may pass on (a grant's scope is immutable, so this is made delegable, not edited into it)
    parent_id = w.db(
        lambda c: make_admin(
            c, w.tenant, w.admin, ACTIONS, RESOURCES, release=False, delegable=True
        )
    )
    represented = w.owner(
        "SELECT represented_principal_id FROM authz.authority_grant WHERE authority_grant_id = :g",
        g=uuid.UUID(parent_id),
    )[0][0]
    parent = (parent_id, represented)
    now = datetime.now(UTC)
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        made = http.post(
            "/v1/authority-grants",
            headers={**w.bearer(w.admin), "Idempotency-Key": "grant-evidence-0001"},
            json={
                "grantee_principal_id": w.approver,
                "represented_principal_id": str(parent[1]),
                "parent_grant_id": str(parent[0]),
                "actions": ["access_readiness.read"],
                "resources": ["agent:*"],
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_to": (now + timedelta(days=10)).isoformat(),
                "risk_class": 1,
            },
        )
    assert made.status_code == 201, made.text
    grant_id = made.json()["authority_grant_id"]
    ref = w.owner(
        "SELECT evidence_ref FROM authz.authority_grant WHERE authority_grant_id = :g",
        g=uuid.UUID(grant_id),
    )[0][0]
    audit = w.owner(
        "SELECT audit_id FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'authority.grant.issued' AND target_ref = :r",
        r=grant_id,
    )
    assert ref is not None and [str(ref)] == [
        str(a[0]) for a in audit
    ]  # exactly the record of this issuance


def test_a_bound_client_stops_being_a_proof_while_api_agents_are_off_and_returns_when_they_are_on(
    rt_url: str, owner_url: str
) -> None:
    """CR-144: nothing is deleted when the feature is switched off, so the binding and the agent are exactly as they were when it is switched back on."""
    from zid_platform import api_agents_switch

    w = approved_world(rt_url, owner_url)
    drop_container_proof(w)
    _build, binding = bind_api_client(w)

    def status() -> Any:
        return w.db(lambda c: agent_identity_status(c, w.daara, now=datetime.now(UTC)))

    try:
        api_agents_switch.configure("on", "production")
        assert status().proof_kind == "API_CLIENT"
        api_agents_switch.configure("off", "production")
        off = status()
        assert off.proof_kind == "NONE" and not off.attested and off.live_bindings == 0
        denied = execute(w, make_run(w, "idem-switch-off")["run_id"])
        assert denied["state"] == "DENIED" and "AGENT_NOT_ATTESTED" in denied["denial_codes"]
        api_agents_switch.configure("on", "production")
        assert status().proof_kind == "API_CLIENT" and status().attestation_id == str(binding)
        assert execute(w, make_run(w, "idem-switch-on")["run_id"])["state"] in OK
    finally:
        api_agents_switch.reset()
