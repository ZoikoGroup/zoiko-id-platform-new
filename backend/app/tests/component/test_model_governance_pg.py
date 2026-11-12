"""Model inventory, independent evaluation, promotion, drift hold and rollback over HTTP against PostgreSQL (ZID-PRI-004 PR-16; AT-015 to AT-017)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from onboarding_fixture import onboard
from test_v2_privacy_pg import ACTIONS, RESOURCES, api_for, code, runtime  # noqa: F401  (runtime is an autouse fixture)


def metrics(kind: str = "face_match") -> dict[str, Any]:
    names = {"face_match": ("fmr", "fnmr", "failure_to_acquire")}[kind]
    return {n: {"value": 0.01, "ci_low": 0.004, "ci_high": 0.02} for n in names}


def test_a_model_is_promoted_only_on_independent_evidence_held_on_drift_and_rolled_back_on_evidence(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=(*RESOURCES, "model:*", "model:new"))
    owner, second = o.bearer(o.owner), o.bearer(o.second)
    reg = {"model_ref": "face-match", "version": "2.0", "kind": "face_match", "provider_service_ref": "veriff:face:uk",
           "live_features": ["capture_quality", "template_distance"]}  # fmt: skip
    ev = {"model_ref": "face-match", "version": "2.0", "evaluator_ref": "lab-1", "independent": True, "cohorts": 5,
          "metrics": metrics(), "accessible_alternative_confirmed": True, "audit_attributes": ["sex", "skin_tone"]}  # fmt: skip
    with api_for(rt_url, o) as api:
        assert api.post("/v2/privacy/models", json=reg).status_code == 401

        # AT-017: a protected characteristic or a proxy is never a live feature
        bad = api.post(
            "/v2/privacy/models",
            headers=owner,
            json={**reg, "live_features": ["capture_quality", "skin_tone_score"]},
        )
        assert bad.status_code == 422 and code(bad) == "protected_feature_in_live_use", bad.text

        made = api.post("/v2/privacy/models", headers=owner, json=reg)
        assert made.status_code == 201 and made.json()["state"] == "REGISTERED", made.text
        assert api.post("/v2/privacy/models", headers=owner, json=reg).status_code == 409, (
            "a version is never re-registered"
        )

        # AT-015: no evidence, no promotion
        blocked = api.post("/v2/privacy/models/face-match/versions/2.0:promote", headers=second)
        assert (
            blocked.status_code == 409 and "FAIRNESS_EVIDENCE_MISSING" in blocked.json()["detail"]
        ), blocked.text

        # audit attributes are kept apart from live inputs
        clash = api.post(
            "/v2/privacy/model-evaluations",
            headers=owner,
            json={**ev, "audit_attributes": ["capture_quality"]},
        )
        assert clash.status_code == 422 and code(clash) == "audit_attribute_is_a_live_feature"

        # a vendor's own, non-independent evaluation does not unlock it
        self_marked = api.post(
            "/v2/privacy/model-evaluations", headers=owner, json={**ev, "independent": False}
        )
        assert (
            self_marked.status_code == 201 and self_marked.json()["usable_for_promotion"] is False
        )
        again = api.post("/v2/privacy/models/face-match/versions/2.0:promote", headers=second)
        assert again.status_code == 409 and "EVALUATION_NOT_INDEPENDENT" in again.json()["detail"]

        # an evaluation with a missing measure is recorded with its problems and cannot be used
        partial = {k: v for k, v in metrics().items() if k != "fnmr"}
        weak = api.post(
            "/v2/privacy/model-evaluations", headers=owner, json={**ev, "metrics": partial}
        )
        assert weak.json()["problems"] == ["MEASURE_MISSING_FNMR"]

        good = api.post("/v2/privacy/model-evaluations", headers=owner, json=ev)
        assert good.status_code == 201 and good.json()["usable_for_promotion"] is True, good.text

        # the registrant cannot promote their own model; another person can
        assert (
            api.post(
                "/v2/privacy/models/face-match/versions/2.0:promote", headers=owner
            ).status_code
            == 403
        )
        live = api.post("/v2/privacy/models/face-match/versions/2.0:promote", headers=second)
        assert live.status_code == 200 and live.json()["state"] == "ACTIVE", live.text

        # a release naming the model is ready on the model's side while it is in service, and blocked the moment it is held
        pk = api.post(
            "/v2/privacy/dpia-packages",
            headers=owner,
            json={
                "market": "GB",
                "product": "id-verification",
                "version": "1.0",
                "controller": "Acme Ltd",
                "purpose_code": "IDENTITY_PROOFING",
                "model_version": "face-match@2.0",
            },
        ).json()
        gate = api.get("/v2/privacy/release-gates/GB/id-verification/1.0", headers=owner).json()
        assert not any(b.startswith("MODEL_") for b in gate["blockers"]), gate

        # AT-016: the supplier changes the model: drift is detected and the rollout is blocked
        ok = api.post(
            "/v2/privacy/models/face-match:observe", headers=owner, json={"seen_version": "2.0"}
        ).json()
        assert ok["verdict"] == "OK" and ok["held"] is False
        drift = api.post(
            "/v2/privacy/models/face-match:observe", headers=owner, json={"seen_version": "2.1"}
        ).json()
        assert drift["verdict"] == "DRIFT" and drift["held"] is True
        held = api.get("/v2/privacy/models/face-match/versions/2.0", headers=owner).json()
        assert held["state"] == "HELD" and "drift" in held["state_reason"]
        gate = api.get("/v2/privacy/release-gates/GB/id-verification/1.0", headers=owner).json()
        assert gate["ready"] is False and "MODEL_HELD" in gate["blockers"], gate
        view = api.get(f"/v2/privacy/dpia-packages/{pk['package_id']}", headers=owner).json()
        assert "MODEL_HELD" in view["release"]["blockers"]

        # the new version has no evidence, so it cannot simply replace the held one
        api.post("/v2/privacy/models", headers=owner, json={**reg, "version": "2.1"})
        assert (
            api.post(
                "/v2/privacy/models/face-match/versions/2.1:promote", headers=second
            ).status_code
            == 409
        )

        # a rollback to the evaluated version is allowed on its own current evidence, and keeps the audit trail
        back = api.post("/v2/privacy/models/face-match/versions/2.0:rollback", headers=second)
        assert back.status_code == 200 and back.json()["state"] == "ACTIVE", back.text
        assert (
            api.post(
                "/v2/privacy/models/face-match:observe", headers=owner, json={"seen_version": "2.0"}
            ).json()["verdict"]
            == "OK"
        )
        assert datetime.now(UTC).year >= 2026
