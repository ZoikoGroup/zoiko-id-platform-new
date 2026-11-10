"""Approved builds: the reviewed software a blueprint release may run (CR-064; ZID-MACHINE-001 §3, §9).

Full app against PostgreSQL with RLS and real access tokens. A build is registered by one person and approved by another; where
approved builds are required (everywhere but local, test and dev) a blueprint release can be drafted only from an approved build;
revoking a build revokes its releases and suspends what runs it; a tenant never sees another's builds; the two-person rule also
holds in the database.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_workload_identity_pg import DIGEST as SEEDED_DIGEST
from test_workload_identity_pg import DOMAIN, seed_agent
from zid_context.spiffe import TrustPolicy
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.entrypoints.attestor_sync import approved_by_workload
from zid_platform.main import create_app
from zid_platform.modules.evidence.application.runtime import configure_runtime, development_runtime
from zid_platform.modules.machine.application import agents as service
from zid_platform.modules.machine.application import workload
from zid_platform.modules.machine.domain.agents import (
    Attestation,
    BindingStatus,
    MachineError,
    WorkloadBinding,
)
from zid_platform.modules.machine.infrastructure.machine_store import save_binding
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
ACTIONS = ["machine.agent.read", "machine.build.register", "machine.build.manage", "machine.blueprint.manage"]
RESOURCES = ["build:*", "blueprint:*"]
BASE = "/v1/machine"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
MANIFEST = "sha256:" + "b" * 64


def uid() -> str:
    return str(uuid.uuid4())


def _app(rt_url: str, ring: KeyRing, **extra: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", **extra),
        keyring=ring,
    )


def test_a_build_is_registered_by_one_person_approved_by_another_and_required_for_a_release(rt_url: str) -> None:
    tenant, registrar, reviewer, nobody, publisher = uid(), uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-builds"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (registrar, reviewer, nobody, publisher):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(c, tenant, [registrar, reviewer, publisher], ACTIONS, RESOURCES)

    async def session(p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(sid, tenant, p, t - timedelta(seconds=30), "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8)),
            )
        return sid

    asyncio.run(seed())
    sessions = {p: asyncio.run(session(p)) for p in (registrar, reviewer, nobody, publisher)}

    def bearer(sub: str) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="admin-console",
            now=datetime.now(UTC), session_id=sessions[sub], scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    reg, rev, non, pub = bearer(registrar), bearer(reviewer), bearer(nobody), bearer(publisher)

    # ---- the catalogue itself (not enforced in the test environment)
    with TestClient(_app(rt_url, ring), base_url=ISS) as http:
        assert http.get(f"{BASE}/builds", headers=non).status_code == 403
        assert http.post(f"{BASE}/builds", headers=non, json={"name": "x", "artifact_digest": D1}).status_code in (403, 422)

        assert http.post(f"{BASE}/builds", headers=reg, json={"name": "x", "artifact_digest": "sha256:short"}).status_code == 422
        made = http.post(f"{BASE}/builds", headers=reg, json={"name": "Invoice bot image", "artifact_digest": "sha-256:" + "1" * 64, "source_ref": "ghcr.io/acme/invoice-bot"})
        assert made.status_code == 201, made.text
        build = made.json()
        assert build["review_status"] == "DRAFT" and build["registered_by"] == registrar and build["approved_by"] is None
        assert build["artifact_digest"] == D1, "sha-256: and sha256: name the same build and are stored in one form"
        again = http.post(f"{BASE}/builds", headers=reg, json={"name": "Same again", "artifact_digest": D1})
        assert again.status_code == 409 and again.json()["error"]["code"] == "BUILD_EXISTS"

        # ---- the person who registered a build cannot approve it
        own = http.post(f"{BASE}/builds/{build['build_id']}:approve", headers=reg)
        assert own.status_code == 403 and own.json()["error"]["code"] == "AGENT_REVIEW_NOT_INDEPENDENT"
        ok = http.post(f"{BASE}/builds/{build['build_id']}:approve", headers=rev)
        assert ok.status_code == 200 and ok.json()["review_status"] == "APPROVED" and ok.json()["approved_by"] == reviewer
        assert http.post(f"{BASE}/builds/{build['build_id']}:approve", headers=rev).status_code == 409  # already approved
        assert http.post(f"{BASE}/builds/{uid()}:approve", headers=rev).status_code == 404

        listing = http.get(f"{BASE}/builds", headers=reg).json()
        assert listing["enforced"] is False and [b["build_id"] for b in listing["items"]] == [build["build_id"]]
        assert http.get(f"{BASE}/builds?status=DRAFT", headers=reg).json()["items"] == []
        assert len(http.get(f"{BASE}/builds?status=APPROVED", headers=reg).json()["items"]) == 1

        # a second build, left as a draft
        draft = http.post(f"{BASE}/builds", headers=reg, json={"name": "Draft build", "artifact_digest": D2}).json()

        # without enforcement any well-formed digest still backs a release (local, test and dev)
        free = http.post(f"{BASE}/blueprints", headers=pub, json={"blueprint_id": "free-bot", "version": "1.0.0", "artifact_digest": D3, "tool_manifest_digest": MANIFEST, "publisher_principal_id": publisher})
        assert free.status_code == 201

    # ---- enforced: a release must run an approved build
    with TestClient(_app(rt_url, ring, agent_builds="enforce"), base_url=ISS) as http:
        assert http.get(f"{BASE}/builds", headers=reg).json()["enforced"] is True

        def release(digest: str, version: str) -> Any:
            return http.post(f"{BASE}/blueprints", headers=pub, json={"blueprint_id": "bot", "version": version, "artifact_digest": digest, "tool_manifest_digest": MANIFEST, "publisher_principal_id": publisher})

        unknown = release(D3, "1.0.0")
        assert unknown.status_code == 409 and unknown.json()["error"]["code"] == "BUILD_NOT_APPROVED"
        notyet = release(D2, "1.0.0")
        assert notyet.status_code == 409 and notyet.json()["error"]["code"] == "BUILD_NOT_APPROVED" and "draft" in notyet.json()["error"]["message"]
        assert release(D1, "1.0.0").status_code == 201
        assert release("sha-256:" + "1" * 64, "1.0.1").status_code == 201, "either spelling of the digest finds the build"

        # ---- revoking the approved build revokes what was drafted from it, and then nothing can use it
        gone = http.post(f"{BASE}/builds/{build['build_id']}:revoke", headers=rev, json={"reason_code": "SUPPLY_CHAIN_COMPROMISE"})
        assert gone.status_code == 200, gone.text
        assert gone.json()["review_status"] == "REVOKED" and gone.json()["releases_revoked"] == 2
        assert http.post(f"{BASE}/builds/{build['build_id']}:revoke", headers=rev, json={}).status_code == 409
        assert release(D1, "1.0.2").status_code == 409
        states = {b["version"]: b["review_status"] for b in http.get(f"{BASE}/blueprints", headers=reg).json()["items"] if b["blueprint_id"] == "bot"}
        assert states == {"1.0.0": "REVOKED", "1.0.1": "REVOKED"}
        assert draft["review_status"] == "DRAFT"


def test_revoking_a_build_suspends_what_runs_it(rt_url: str) -> None:
    tenant, agent_id, actor = uid(), uid(), uid()

    async def run() -> dict[str, Any]:
        configure_runtime(development_runtime("uk-1"))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_agent(c, tenant, agent_id)  # an ACTIVE agent on blueprint bp_pay@1.0.0 (an approved release)
            binding_id = uid()
            await save_binding(
                c,
                tenant,
                WorkloadBinding(
                    binding_id, agent_id, f"spiffe://x/{binding_id}", "1.0.0", SEEDED_DIGEST, "prod", Attestation.NONE,
                    BindingStatus.ACTIVE, datetime.now(UTC) - timedelta(minutes=1), datetime.now(UTC) + timedelta(days=30),
                ),
            )
            built = await service.register_build(c, tenant, name="pay bot", artifact_digest=SEEDED_DIGEST, source_ref=None, provenance_ref=None, actor=uid())
            await service.approve_build(c, tenant, built["build_id"], actor=actor)
            out = await service.revoke_build(c, tenant, built["build_id"], reason="BAD_BUILD", actor=actor)
            status = (await c.execute(text("SELECT status FROM agent.agent_profile WHERE agent_principal_id = :a"), {"a": uuid.UUID(agent_id)})).scalar_one()
            binding = (await c.execute(text("SELECT status FROM agent.workload_binding WHERE binding_id = :b"), {"b": uuid.UUID(binding_id)})).scalar_one()
            release = (await c.execute(text("SELECT review_status FROM agent.blueprint_release WHERE blueprint_id = 'bp_pay'"))).scalar_one()
            return {**out, "agent": status, "binding": binding, "release": release}

    got = asyncio.run(run())
    assert (got["releases_revoked"], got["bindings_suspended"], got["agents_suspended"]) == (1, 1, 1)
    assert (got["agent"], got["binding"], got["release"]) == ("SUSPENDED", "SUSPENDED", "REVOKED")


def test_the_database_holds_the_two_person_rule_and_the_tenant_boundary(rt_url: str, owner_url: str) -> None:
    tenant, other, registrar = uid(), uid(), uid()

    async def run() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            built = await service.register_build(c, tenant, name="mine", artifact_digest=D1, source_ref=None, provenance_ref=None, actor=registrar)
        # an APPROVED row naming its own registrar as approver is refused by the table, not only by the service
        async with engine(owner_url) as eng:
            with pytest.raises(DBAPIError):
                async with eng.begin() as c:
                    await c.execute(
                        text("UPDATE agent.build_artifact SET review_status = 'APPROVED', approved_by = :r, approved_at = now() WHERE build_id = :b"),
                        {"r": uuid.UUID(registrar), "b": uuid.UUID(built["build_id"])},
                    )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(other)) as c:
            return int((await c.execute(text("SELECT count(*) FROM agent.build_artifact"))).scalar_one())

    assert asyncio.run(run()) == 0, "another tenant's builds are invisible"


def test_builds_are_required_by_default_outside_local_test_and_dev() -> None:
    from types import SimpleNamespace

    from zid_platform.modules.machine.api.agents import builds_enforced

    def req(mode: str, env: str) -> Any:
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_builds=mode, environment=env)))

    for env in ("local", "test", "dev"):
        assert builds_enforced(req("auto", env)) is False
    for env in ("staging", "production", "prod"):
        assert builds_enforced(req("auto", env)) is True
    assert builds_enforced(req("enforce", "local")) is True
    assert builds_enforced(req("off", "local")) is False


def test_a_binding_runs_the_software_of_its_agents_approved_release(rt_url: str) -> None:
    """A workload binding names the digest of the release its agent is built from: not any digest, not another version, not an
    unapproved release, and (where approved builds are required) not a build nobody approved."""
    tenant, agent_id, registrar, approver = uid(), uid(), uid(), uid()
    policy = TrustPolicy(DOMAIN)

    def sid(n: int) -> str:
        return f"spiffe://{DOMAIN}/t/{tenant}/ns/x/sa/w{n}"

    async def bind(c: Any, n: int, *, digest: str = SEEDED_DIGEST, version: str = "1.0.0", enforced: bool = False) -> Any:
        return await workload.register_binding(
            c, tenant, policy=policy, agent_principal_id=agent_id, spiffe_id=sid(n), blueprint_version=version,
            artifact_digest=digest, environment_id="prod", attestation_required=2,
            valid_to=datetime.now(UTC) + timedelta(days=1), actor=uid(), require_approved_build=enforced,
        )

    async def refused(c: Any, n: int, code: str, **kw: Any) -> None:
        async with c.begin_nested():
            with pytest.raises(MachineError) as e:
                await bind(c, n, **kw)
            assert e.value.code == code, e.value.code

    async def run() -> None:
        configure_runtime(development_runtime("uk-1"))
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await seed_agent(c, tenant, agent_id)  # ACTIVE agent on bp_pay@1.0.0, an approved release of SEEDED_DIGEST

            await refused(c, 1, "WORKLOAD_ARTIFACT_NOT_APPROVED", digest=D1)  # any other digest
            await refused(c, 2, "WORKLOAD_ARTIFACT_NOT_APPROVED", version="9.9.9")  # right digest, another version
            await refused(c, 3, "WORKLOAD_ARTIFACT_NOT_APPROVED", digest="notadigest")

            ok = await bind(c, 4)
            assert ok.artifact_digest == SEEDED_DIGEST
            spelled = await bind(c, 5, digest=SEEDED_DIGEST.replace("sha256:", "sha-256:"))  # either spelling is the same digest
            assert spelled.binding_id

            # where approved builds are required, the release's own build must be approved too
            await refused(c, 6, "BUILD_NOT_APPROVED", enforced=True)
            built = await service.register_build(c, tenant, name="pay bot", artifact_digest=SEEDED_DIGEST, source_ref=None, provenance_ref=None, actor=registrar)
            await refused(c, 7, "BUILD_NOT_APPROVED", enforced=True)  # registered, not yet approved
            await service.approve_build(c, tenant, built["build_id"], actor=approver)
            assert (await bind(c, 8, enforced=True)).binding_id

            # what the attestor registration would list: the approved build's digest, for the bindings that run it
            found = await approved_by_workload(c)
            assert found[sid(8)] == frozenset({SEEDED_DIGEST})
            assert found[sid(5)] == frozenset({SEEDED_DIGEST}), "a binding spelled sha-256: matches its build all the same"

            # a release that is not approved (here: revoked) cannot back a new binding
            await c.execute(text("UPDATE agent.blueprint_release SET review_status = 'REVOKED' WHERE blueprint_id = 'bp_pay'"))
            await refused(c, 9, "AGENT_BLUEPRINT_NOT_APPROVED")

    asyncio.run(run())


def test_an_image_name_is_looked_up_only_in_registries_the_operator_named_and_only_for_those_who_may(rt_url: str) -> None:
    """The route changes nothing: it returns the digest an image name points at. It is off until an operator names registries, needs the build
    authority, and contacts nothing that was not named."""
    import hashlib

    tenant, engineer, nobody = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-lookup"))
    manifest = b'{"schemaVersion":2,"layers":[]}'
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    asked: list[str] = []

    def registry_handler(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        if req.url.path.endswith("/tags/list"):
            return httpx.Response(200, json={"name": "acme/bot", "tags": ["1.3", "1.4", "sha256-" + "a" * 64 + ".sig"]})
        return httpx.Response(200, content=manifest, headers={"content-type": "application/vnd.oci.image.manifest.v1+json"})

    async def seed() -> dict[str, str]:
        out = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p in (engineer, nobody):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(c, tenant, [engineer], ACTIONS, RESOURCES)
            for p in (engineer, nobody):
                sid, t = uid(), datetime.now(UTC)
                principal, _ = await load_principal(c, p)
                await create_session(c, Session(sid, tenant, p, t - timedelta(seconds=30), "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8)))
                out[p] = sid
        return out

    sessions = asyncio.run(seed())

    def bearer(sub: str) -> dict[str, str]:
        tok = issue_access_token(ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="admin-console",
                                 now=datetime.now(UTC), session_id=sessions[sub], scope=frozenset())
        return {"Authorization": f"Bearer {tok}"}

    eng_h, non_h = bearer(engineer), bearer(nobody)
    url = f"{BASE}/builds:resolve"

    # off by default: nothing is contacted and the console is told the lookup is not available
    with TestClient(_app(rt_url, ring), base_url=ISS) as http:
        http.app.state.registry_transport = httpx.MockTransport(registry_handler)  # type: ignore[attr-defined]
        assert http.get(f"{BASE}/builds", headers=eng_h).json()["lookup_enabled"] is False
        off = http.post(url, headers=eng_h, json={"image": "ghcr.io/acme/bot:1"})
        assert off.status_code == 409 and off.json()["error"]["code"] == "REGISTRY_LOOKUP_DISABLED"
        assert http.post(f"{BASE}/builds:versions", headers=eng_h, json={"image": "ghcr.io/acme/bot"}).json()["error"]["code"] == "REGISTRY_LOOKUP_DISABLED"
        assert asked == []

    with TestClient(_app(rt_url, ring, build_registries=("ghcr.io",), build_images=("ghcr.io/acme/bot",)), base_url=ISS) as http:
        http.app.state.registry_transport = httpx.MockTransport(registry_handler)  # type: ignore[attr-defined]
        http.app.state.registry_resolver = lambda host: {"8.8.8.8"}  # type: ignore[attr-defined]
        listed = http.get(f"{BASE}/builds", headers=eng_h).json()
        assert listed["lookup_enabled"] is True and listed["images"] == ["ghcr.io/acme/bot"]  # offered to pick, not typed
        assert http.post(url, headers=non_h, json={"image": "ghcr.io/acme/bot:1"}).status_code == 403  # no build authority
        assert http.post(f"{BASE}/builds:versions", headers=non_h, json={"image": "ghcr.io/acme/bot"}).status_code == 403
        assert asked == []

        versions = http.post(f"{BASE}/builds:versions", headers=eng_h, json={"image": "ghcr.io/acme/bot"})
        assert versions.status_code == 200, versions.text
        assert versions.json()["tags"] == ["1.3", "1.4"] and versions.json()["repository"] == "acme/bot"  # a signature tag is not a version
        assert asked == ["https://ghcr.io/v2/acme/bot/tags/list?n=1000"]
        asked.clear()

        got = http.post(url, headers=eng_h, json={"image": "ghcr.io/acme/bot:1.4"})
        assert got.status_code == 200, got.text
        assert got.json()["digest"] == digest and got.json()["repository"] == "acme/bot" and got.json()["reference"] == "1.4"
        assert asked == ["https://ghcr.io/v2/acme/bot/manifests/1.4"]

        # nothing is registered by looking: the engineer registers what came back, as before
        assert http.get(f"{BASE}/builds", headers=eng_h).json()["items"] == []
        assert http.post(f"{BASE}/builds", headers=eng_h, json={"name": "Bot 1.4", "artifact_digest": got.json()["digest"], "source_ref": "ghcr.io/acme/bot:1.4"}).status_code == 201

        before = len(asked)
        other = http.post(url, headers=eng_h, json={"image": "evil.example.com/acme/bot:1"})
        assert other.status_code == 422 and other.json()["error"]["code"] == "REGISTRY_NOT_ALLOWED"
        bad = http.post(url, headers=eng_h, json={"image": "https://ghcr.io/acme/bot"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "IMAGE_NAME_INVALID"
        assert http.post(f"{BASE}/builds:versions", headers=eng_h, json={"image": "evil.example.com/acme/bot"}).status_code == 422
        assert len(asked) == before, "nothing that was not named, and nothing malformed, was ever contacted"


def test_a_build_pipeline_can_register_a_build_but_never_approve_or_revoke_one(rt_url: str) -> None:
    """The authority to register is separate from the authority to approve. A pipeline (an application principal) holds only the first, so a
    compromised pipeline can propose a build, and a person still has to approve it."""
    tenant, pipeline, approver, approver_only = uid(), uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-pipeline"))

    async def seed() -> dict[str, str]:
        out = {}
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await create_principal(c, Principal(pipeline, tenant, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, "uk-1"))
            for p in (approver, approver_only):
                await create_principal(c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"))
            await make_admin(c, tenant, [pipeline], ["machine.agent.read", "machine.build.register"], ["build:*"])
            # one policy release per tenant: the later grants reuse it
            await make_admin(c, tenant, [approver], ["machine.agent.read", "machine.build.register", "machine.build.manage"], ["build:*"], release=False)
            await make_admin(c, tenant, [approver_only], ["machine.agent.read", "machine.build.manage"], ["build:*"], release=False)
            for p in (pipeline, approver, approver_only):
                sid, t = uid(), datetime.now(UTC)
                principal, _ = await load_principal(c, p)
                await create_session(c, Session(sid, tenant, p, t - timedelta(seconds=30), "native:webauthn", 2, principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8)))
                out[p] = sid
        return out

    sessions = asyncio.run(seed())

    def bearer(sub: str) -> dict[str, str]:
        tok = issue_access_token(ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="ci-pipeline",
                                 now=datetime.now(UTC), session_id=sessions[sub], scope=frozenset())
        return {"Authorization": f"Bearer {tok}"}

    ci, boss, only = bearer(pipeline), bearer(approver), bearer(approver_only)
    with TestClient(_app(rt_url, ring), base_url=ISS) as http:
        made = http.post(f"{BASE}/builds", headers=ci, json={"name": "Nightly image", "artifact_digest": D1, "source_ref": "ghcr.io/acme/bot:nightly"})
        assert made.status_code == 201 and made.json()["registered_by"] == pipeline and made.json()["review_status"] == "DRAFT"
        bid = made.json()["build_id"]

        # the pipeline can propose and look, but cannot approve its own build, revoke anything, or draft a release from it
        assert http.get(f"{BASE}/builds", headers=ci).status_code == 200
        assert http.post(f"{BASE}/builds/{bid}:approve", headers=ci).status_code == 403
        assert http.post(f"{BASE}/builds/{bid}:revoke", headers=ci, json={}).status_code == 403

        # approving is a different authority, and holding it alone does not allow registering
        assert http.post(f"{BASE}/builds", headers=only, json={"name": "Not allowed", "artifact_digest": D2}).status_code == 403
        ok = http.post(f"{BASE}/builds/{bid}:approve", headers=only)
        assert ok.status_code == 200 and ok.json()["approved_by"] == approver_only and ok.json()["registered_by"] == pipeline

        # a person holding both can do each, but still not approve what they registered themselves
        mine = http.post(f"{BASE}/builds", headers=boss, json={"name": "Mine", "artifact_digest": D3})
        assert mine.status_code == 201
        assert http.post(f"{BASE}/builds/{mine.json()['build_id']}:approve", headers=boss).status_code == 403
