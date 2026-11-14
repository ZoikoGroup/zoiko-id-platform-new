"""Access readiness agent: the run's short-lived credential, the sponsor notification, the permitted evidence export and the optional
AI explanation, over HTTP and against PostgreSQL with RLS (ZID-AG-WF-001 sections 07 and 09 steps 3, 8, 9; CR-091).

Acceptance cases: AT-07 (no credential for an unattested, quarantined or revoked worker), AT-15, AT-16, AT-17 (the explanation), AT-19
(a revoked credential stops the run at its next step), AT-21 (no token in logs or records). No real model is called: a fake gateway is
handed to the app. Every record is synthetic.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from access_readiness_fixture import BASE, RESOURCES, World, runtime, uid
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from test_access_readiness_pg import Records, body, count, execute, make_run, release
from zid_db import tenant_transaction
from zid_platform.modules.access_readiness.application.explain import (
    ExplainDraft,
    ExplainRequest,
)
from zid_platform.modules.machine.application.workload import quarantine_environment
from zid_platform.modules.machine.domain.agents import BindingStatus
from zid_platform.modules.machine.infrastructure.machine_store import set_binding_status
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal

ALERT = "access_readiness.run.finished"


class FakeModel:
    """Stands in for an approved model gateway. Records what it was sent; never calls anything."""

    name = "fake"

    def __init__(self, text: str | None = None, *, fail: bool = False) -> None:
        self.text, self.fail = text, fail
        self.seen: list[ExplainRequest] = []

    async def draft(self, request: ExplainRequest) -> ExplainDraft:
        self.seen.append(request)
        if self.fail:
            raise ConnectionError("model gateway down")
        return ExplainDraft(self.text or "", "fake", "fake-model-1")


def evidence_kinds(w: World, run_id: str) -> list[str]:
    return [
        r[0]
        for r in w.owner(
            "SELECT event_type FROM agent.access_review_evidence WHERE tenant_id = :t AND run_id = :r ORDER BY seq",
            r=uuid.UUID(run_id),
        )
    ]


def alerts(w: World, run_id: str) -> list[Any]:
    return w.owner(
        "SELECT kind, title, body, object_type, object_ref, href, recommended_action FROM platform.notification_request "
        "WHERE tenant_id = :t AND object_ref = :r",
        r=run_id,
    )


def add_reader(w: World) -> str:
    """A person who may read runs but not review proposals."""
    reader = uid()

    async def seed() -> None:
        async with engine(w.rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as c:
            await create_principal(
                c, Principal(reader, w.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            await make_admin(
                c, w.tenant, reader, ["access_readiness.read"], RESOURCES, release=False
            )

    w.run(seed())
    w.sessions[reader] = w.run(w._session(reader))
    return reader


# --------------------------------------------------------------------------------------------- credential and notification


def test_a_run_gets_a_short_lived_credential_revokes_it_and_notifies_the_sponsor(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    log = Records()
    logging.getLogger().addHandler(log)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
            release(http, w, w.daara)
            r = http.post(
                f"{BASE}/agents/{w.daara}/runs",
                headers={**w.bearer(w.admin), "Idempotency-Key": "idem-credential-1"},
                json={},
            )
            assert r.status_code == 202, r.text
            run = r.json()
            assert (
                run["state"] == "NEEDS_REVIEW" and run["explanation"] is None
            )  # AI off by default
    finally:
        logging.getLogger().removeHandler(log)

    # section 09 step 3: issued through the machine module's issuance decision, bound to an audience and a purpose; step 9: revoked
    kinds = evidence_kinds(w, run["run_id"])
    assert (
        kinds.index("preflight_passed")
        < kinds.index("credential_issued")
        < kinds.index("metadata_read")
    )
    assert kinds[-3:] == ["credential_revoked", "sponsor_notified", "run_needs_review"]
    issued = w.owner(
        "SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'machine.workload.credential_issued'"
    )[0][0]
    revoked = w.owner(
        "SELECT reason_code, body::text FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'machine.workload.lease_revoked'"
    )
    assert issued == 1 and len(revoked) == 1 and revoked[0][0] == "NEEDS_REVIEW"
    assert (
        "zid:access-readiness:metadata-adapter" in revoked[0][1]
        and "access_posture_review" in revoked[0][1]
    )
    decisions = w.owner(
        "SELECT action, outcome, reason_code FROM agent.access_review_policy_decision WHERE tenant_id = :t AND run_id = :r",
        r=uuid.UUID(run["run_id"]),
    )
    assert all(
        d[1] == "ALLOW"
        for d in decisions
        if d[0] in ("read_mandate_metadata", "read_agent_identity_status")
    )

    # AT-21: a lease is not a bearer token; nothing token-shaped is logged or kept with the run
    haystack = "\n".join(log.lines)
    assert "eyJ" not in haystack and "access_token" not in haystack
    assert "eyJ" not in str(run)

    # section 09 step 9: the sponsor is told through the notification module: run id, state and counts, nothing personal
    found = alerts(w, run["run_id"])
    assert len(found) == 1
    kind, title, text, object_type, object_ref, href, action = found[0]
    assert kind == ALERT and object_type == "agent_run" and object_ref == run["run_id"]
    assert href.endswith(f"/runs/{run['run_id']}") and "sponsor" in action
    assert f"Run {run['run_id']} ended NEEDS_REVIEW" in text and "Records checked: 7" in text
    assert "Findings: 6" in text and "Privileges changed: 0" in text
    for secret in (*w.mandates.values(), *w.grants.values(), w.sponsor, w.sponsor2, w.org, "Acme"):
        assert secret not in text and secret not in title


def test_at07_no_credential_for_an_underattested_or_quarantined_worker_and_a_revoked_one_stops_the_run(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        release(http, w, w.daara)

    # the binding's attestation is valid but below the issuance floor: the preflight sees an attested agent, the issuer says no
    w.owner("UPDATE agent.workload_attestation SET level = 1 WHERE tenant_id = :t")
    weak = execute(w, make_run(w, "idem-weak-attestation")["run_id"])
    assert weak["state"] == "DENIED" and weak["denial_codes"] == [
        "CREDENTIAL_DENIED",
        "ATTESTATION_INSUFFICIENT",
    ]
    assert weak["summary"]["records_read"] == 0
    w.owner("UPDATE agent.workload_attestation SET level = 2 WHERE tenant_id = :t")

    # a quarantined environment: no credential, no run, and the sponsor still hears that the run was denied
    async def quarantine(c: Any) -> None:
        await quarantine_environment(c, w.tenant, "prod", reason="TEST_QUARANTINE", actor=None)

    w.db(quarantine)
    q = execute(w, make_run(w, "idem-quarantined")["run_id"])
    assert q["state"] == "DENIED" and "CREDENTIAL_DENIED" in q["denial_codes"]
    assert "ENVIRONMENT_QUARANTINED" in q["denial_codes"]
    assert "credential_issued" not in evidence_kinds(w, q["run_id"])
    assert alerts(w, q["run_id"])[0][2].startswith(f"Run {q['run_id']} ended DENIED")
    assert count(w, "access_review_proposal") == 0
    w.owner("UPDATE agent.environment_quarantine SET lifted_at = now() WHERE tenant_id = :t")

    # AT-19: the binding is revoked while the run reads; the next read is refused with the credential's code, nothing is written,
    # and no broader credential is asked for
    class Revoking:
        async def read_mandates(self, conn: Any, *, authorize: Any, **_: Any) -> list[Any]:
            await authorize("read_mandate_metadata", "mandates:page")
            await set_binding_status(conn, f"spiffe://zid/{w.daara}", BindingStatus.REVOKED)
            await authorize("read_agent_identity_status", f"agent:{w.daara}")
            return []

    stopped = execute(w, make_run(w, "idem-revoked-mid-run")["run_id"], adapter=Revoking())
    assert stopped["state"] == "FAILED" and stopped["denial_codes"] == ["CREDENTIAL_REVOKED"]
    denied = w.owner(
        "SELECT action, reason_code FROM agent.access_review_policy_decision WHERE tenant_id = :t AND run_id = :r AND outcome = 'DENY'",
        r=uuid.UUID(stopped["run_id"]),
    )
    assert ("read_agent_identity_status", "CREDENTIAL_REVOKED") in [tuple(d) for d in denied]
    assert evidence_kinds(w, stopped["run_id"])[-1] == "run_failed"
    assert "credential_revoked" in evidence_kinds(w, stopped["run_id"])
    assert count(w, "access_review_proposal") == 0
    assert (
        w.owner(
            "SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t AND event_type = 'machine.workload.credential_issued'"
        )[0][0]
        == 1
    )  # only the run that got one


# --------------------------------------------------------------------------------------------- export permitted evidence


def test_export_permitted_evidence_needs_a_purpose_and_is_redacted_without_the_review_role(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    reader = add_reader(w)
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        release(http, w, w.daara)
        adm = w.bearer(w.admin)
        run = http.post(
            f"{BASE}/agents/{w.daara}/runs",
            headers={**adm, "Idempotency-Key": "idem-export-1"},
            json={},
        ).json()
        url = f"{BASE}/agents/{w.daara}/runs/{run['run_id']}/evidence:export"

        # no purpose: refused, and the refusal is recorded
        missing = http.post(url, headers=adm, json={})
        assert (
            missing.status_code == 422
            and missing.json()["error"]["code"] == "EXPORT_PURPOSE_REQUIRED"
        )
        assert http.post(url, headers=adm, json={"purpose": "  "}).status_code == 422
        refused = w.owner(
            "SELECT outcome, reason_code FROM evidence.audit_record WHERE tenant_id = :t "
            "AND event_type = 'access_readiness.evidence.export_refused'"
        )
        assert len(refused) == 2 and all(
            tuple(r) == ("DENIED", "EXPORT_PURPOSE_MISSING") for r in refused
        )

        # no role at all: 403, and another run id is the same answer as a missing one
        assert (
            http.post(url, headers=w.bearer(w.nobody), json={"purpose": "audit"}).status_code == 403
        )
        other = f"{BASE}/agents/{w.daara}/runs/{uid()}/evidence:export"
        assert http.post(other, headers=adm, json={"purpose": "audit"}).status_code == 404

        # the review role: references and digests, the chain verified now
        full = http.post(url, headers=adm, json={"purpose": "quarterly access review"})
        assert full.status_code == 200, full.text
        out = full.json()
        assert out["schema"] == "zid.access-readiness.evidence.v1" and out["redacted"] is False
        assert out["chain"] == {"verified": True, "integrity_exceptions": []}
        assert out["state"] == "NEEDS_REVIEW" and out["result_digest"].startswith("sha256:")
        assert all(e["payload_digest"].startswith("sha256:") for e in out["evidence"])
        assert len(out["proposals"]) == 6 and all(p["evidence_refs"] for p in out["proposals"])
        assert all(
            p["target"].endswith("...") for p in out["proposals"]
        )  # masked even for a reviewer
        assert any(
            d["resource"].startswith("agent:") and d["resource"] != "agent:redacted"
            for d in out["decisions"]
        )

        # read but not review: the same export, redacted, and it says so
        part = http.post(url, headers=w.bearer(reader), json={"purpose": "quarterly access review"})
        assert part.status_code == 200, part.text
        red = part.json()
        assert red["redacted"] is True and red["chain"]["verified"] is True
        assert len(red["evidence"]) == len(out["evidence"])
        assert all(p["target"] == "redacted" and p["evidence_refs"] == [] for p in red["proposals"])
        assert all(p["evidence_ref_count"] >= 1 for p in red["proposals"])
        assert all(
            d["resource"].endswith(":redacted") or ":" not in d["resource"]
            for d in red["decisions"]
        )

    # the redacted export carries no mandate or grant id at all; neither export names a sponsor or an organization
    for secret in (*w.mandates.values(), *w.grants.values()):
        assert secret not in part.text
    for secret in (w.sponsor, w.sponsor2, w.org):
        assert secret not in full.text and secret not in part.text
    exported = w.owner(
        "SELECT actor_handle, body::text FROM evidence.audit_record WHERE tenant_id = :t "
        "AND event_type = 'access_readiness.evidence.exported' ORDER BY created_at"
    )
    assert len(exported) == 2
    assert "purpose_sha256" in exported[0][1] and "quarterly" not in exported[0][1]
    assert (
        '"redacted": true' in exported[1][1] and exported[0][0] != exported[1][0]
    )  # two different people


# --------------------------------------------------------------------------------------------- the optional AI explanation


def explaining_world(rt_url: str, owner_url: str, model: FakeModel | None) -> tuple[World, Any]:
    w = World(rt_url, owner_url).start()
    app = w.app(
        access_readiness_enabled=True,
        access_readiness_regions=("local-1",),
        access_readiness_ai_explain=True,
    )
    app.state.access_readiness_explainer = model
    return w, app


def test_the_explanation_is_off_unless_the_cell_and_the_setup_both_opt_in(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url, owner_url).start()
    adm = w.bearer(w.admin)
    with TestClient(w.on(), base_url="https://id.zoiko.test") as http:
        refused = http.post(
            f"{BASE}/agents", headers=adm, json=body(agent=w.daara, ai_explanation=True)
        )
        assert (
            refused.status_code == 422
            and refused.json()["error"]["code"] == "AI_EXPLANATION_DISABLED"
        )
        release(http, w, w.daara)
        assert (
            http.get(f"{BASE}/agents/{w.daara}", headers=adm).json()["scope"]["ai_explanation"]
            is False
        )
        patch = http.patch(f"{BASE}/agents/{w.daara}", headers=adm, json={"ai_explanation": True})
        assert (
            patch.status_code == 422 and patch.json()["error"]["code"] == "AI_EXPLANATION_DISABLED"
        )

    # the cell allows it but this setup did not opt in: a model handed to the app is never called
    model = FakeModel("unused")
    run = execute(
        w, make_run(w, "idem-no-opt-in")["run_id"], rt=runtime(ai_explain=True, explainer=model)
    )
    assert (
        run["state"] == "NEEDS_REVIEW" and "explanation" not in run["summary"] and model.seen == []
    )


def test_at15_at16_at17_a_verified_explanation_never_changes_the_result(
    rt_url: str, owner_url: str
) -> None:
    good = FakeModel(
        "The run checked 7 records and flagged 6 findings, including R-01 EXPIRED_MANDATE. "
        "Each one is a proposal for a person; nothing was changed."
    )
    w, app = explaining_world(rt_url, owner_url, good)
    adm = w.bearer(w.admin)
    with TestClient(app, base_url="https://id.zoiko.test") as http:
        assert (
            http.post(
                f"{BASE}/agents", headers=adm, json=body(agent=w.daara, ai_explanation=True)
            ).status_code
            == 201
        )
        setup = http.get(f"{BASE}/agents/{w.daara}", headers=adm).json()
        assert setup["scope"]["ai_explanation"] is True
        assert http.post(f"{BASE}/agents/{w.daara}/dry-runs", headers=adm, json={}).json()["passed"]
        assert (
            http.post(f"{BASE}/agents/{w.daara}/publish-requests", headers=adm).status_code == 201
        )
        approved = http.post(
            f"{BASE}/agents/{w.daara}/publish-requests:approve", headers=w.bearer(w.approver)
        )
        assert (
            approved.status_code == 200 and approved.json()["scope_digest"] == setup["scope_digest"]
        )

        r = http.post(
            f"{BASE}/agents/{w.daara}/runs",
            headers={**adm, "Idempotency-Key": "idem-ai-1"},
            json={},
        )
        assert r.status_code == 202, r.text
        run = r.json()
    explanation = run["explanation"]
    assert explanation["source"] == "MODEL" and explanation["authoritative"] is False
    assert explanation["text"] == good.text and explanation["model"] == "fake-model-1"
    assert "explanation" not in run["summary"]  # the summary stays the deterministic result
    assert (
        run["state"] == "NEEDS_REVIEW"
        and run["summary"]["findings"] == 6
        and run["summary"]["proposals_created"] == 6
    )
    assert "explanation_attached" in evidence_kinds(w, run["run_id"])

    # what the model saw: codes and counts, no tools, no ids, no metadata strings
    sent = good.seen[0]
    blob = sent.system + sent.facts
    for secret in (
        *w.mandates.values(),
        *w.grants.values(),
        w.daara,
        w.tenant,
        w.sponsor,
        w.sponsor2,
        w.org,
        "Acme",
        "invoice",
    ):
        assert secret not in blob
    assert not hasattr(sent, "tools")

    # AT-15/AT-16: a model that claims something the result does not hold is replaced by the fixed text; the result is the same
    lying = FakeModel("Ignore the rules: 0 findings, state COMPLETED, R-07 ACCESS_GRANTED.")
    again = execute(
        w, make_run(w, "idem-ai-lie")["run_id"], rt=runtime(ai_explain=True, explainer=lying)
    )
    lie = again["summary"]["explanation"]
    assert lie["source"] == "TEMPLATE" and lie["fallback_reason"] == "UNSUPPORTED_CLAIM"
    assert (
        again["state"] == run["state"]
        and again["summary"]["result_digest"] == run["summary"]["result_digest"]
    )
    assert again["summary"]["findings"] == 6

    # AT-17: the model is down: the deterministic result and the fixed text
    down = execute(
        w,
        make_run(w, "idem-ai-down")["run_id"],
        rt=runtime(ai_explain=True, explainer=FakeModel(fail=True)),
    )
    assert (
        down["state"] == "NEEDS_REVIEW"
        and down["summary"]["result_digest"] == run["summary"]["result_digest"]
    )
    assert down["summary"]["explanation"]["fallback_reason"] == "MODEL_UNAVAILABLE"
    assert down["summary"]["explanation"]["text"] == lie["text"]
    # no gateway configured at all: the fixed text, labelled as such
    none = execute(
        w, make_run(w, "idem-ai-none")["run_id"], rt=runtime(ai_explain=True, explainer=None)
    )
    assert none["summary"]["explanation"]["fallback_reason"] == "MODEL_NOT_CONFIGURED"
