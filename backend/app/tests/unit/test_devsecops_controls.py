"""Static DevSecOps controls (ZID-DEVSECOPS-001, DEC-G4) and the promotion/IaC/secret tooling behind them.

These tests parse the repository's own artifacts (workflows, Dockerfile, Kyverno policies, Helm chart, branch
ruleset, migrations) and run the pure policy tools (`tools/scripts/release_gate.py`, `iac_policy.py`,
`secret_scan.py`). They fail if a control is removed. They do NOT prove behaviour in a live cluster, registry or
GitHub organisation: admission, signature verification and branch protection are asserted as configuration
and, for the gate, as decision logic over verified-evidence records.

Rows: DEVSEC-03, 04, 05, 07, 08, 09, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 23, 24, 25, 26, 28, 29, 30;
MPR-02, MPR-15, MPR-16.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
TOOLS = REPO / "tools/scripts"
WORKFLOWS = REPO / ".github/workflows"
KYVERNO = REPO / "deploy/policies/kyverno"
CHART = REPO / "deploy/helm/charts/zid-platform"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"zid_tool_{name}", TOOLS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


gate = _tool("release_gate")
iac = _tool("iac_policy")
scanner = _tool("secret_scan")
candidate_builder = _tool("build_candidate")


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _yaml(p) for p in sorted(WORKFLOWS.glob("*.yml"))}


def _triggers(wf: dict[str, Any]) -> set[str]:
    on = wf.get("on", wf.get(True))  # YAML 1.1 reads the bare key `on` as boolean True
    return set(on) if isinstance(on, dict) else {on} if isinstance(on, str) else set(on or [])


def _steps(wf: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


def _kyverno(name: str) -> dict[str, Any]:
    return _yaml(KYVERNO / name)


POLICY = json.loads((REPO / "deploy/release/promotion-policy.json").read_text(encoding="utf-8"))
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/zoikogroup/zoiko-id-platform@{DIGEST}"
BUILDER = POLICY["approved_builders"][0]


def good_candidate() -> dict[str, Any]:
    return {
        "image": IMAGE,
        "signature": {
            "verified": True,
            "identity": POLICY["approved_signer_identities"][0],
            "issuer": POLICY["oidc_issuer"],
        },
        "provenance": {
            "verified": True,
            "builder_id": BUILDER,
            "source_repo": POLICY["approved_sources"][0],
            "source_ref": "refs/heads/main",
            "subject_digest": DIGEST,
            "slsa_level": 3,
        },
        "sbom": {
            "format": "CycloneDX",
            "subject_digest": DIGEST,
            "components": ["fastapi@0.115.0", "colors@1.4.0"],
        },
        "findings": [{"id": "CVE-2026-0001", "severity": "HIGH"}],
        "preprod_digest": DIGEST,
    }


def decide(c: dict[str, Any], **kw: Any) -> Any:
    kw.setdefault("target", "prod")
    kw.setdefault(
        "known_exploited", frozenset()
    )  # the feed was fetched and lists nothing that applies
    return gate.evaluate(c, POLICY, now=NOW, **kw)


# ---------------------------------------------------------------- CI pipeline and untrusted PRs (DEVSEC-04, 14)


def test_every_workflow_action_is_pinned_by_sha_and_no_privileged_pr_trigger() -> None:
    """DEVSEC-04: nothing a fork controls runs with secrets; actions cannot be swapped under us."""
    for name, wf in _workflows().items():
        assert "pull_request_target" not in _triggers(wf), (
            f"{name}: pull_request_target gives forks secrets"
        )
        assert wf.get("permissions") == {"contents": "read"}, (
            f"{name}: top-level token must be read-only"
        )
        for step in _steps(wf):
            ref = step.get("uses")
            if ref and not ref.startswith("./"):
                assert re.fullmatch(r".+@[0-9a-f]{40}", ref), (
                    f"{name}: {ref} is not pinned by commit SHA"
                )


def test_pull_request_workflows_hold_no_secret_or_signing_identity_and_release_is_not_pr_triggered() -> (
    None
):
    """DEVSEC-04: untrusted PR cannot read protected secrets or sign/deploy an artifact."""
    for name, wf in _workflows().items():
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        if "pull_request" in _triggers(wf):
            assert not re.search(r"secrets\.(?!GITHUB_TOKEN)", text), (
                f"{name}: PR workflow references a secret"
            )
            for job in wf["jobs"].values():
                perms = job.get("permissions", {})
                assert perms.get("id-token") != "write", (
                    f"{name}: PR workflow can mint an OIDC identity"
                )
                assert perms.get("packages") != "write"
    for name in ("release.yml", "build-image.yml"):
        assert not ({"pull_request", "pull_request_target"} & _triggers(_workflows()[name])), name
    release = _workflows()["release.yml"]
    assert release["jobs"]["build"]["if"].startswith("github.event_name == 'push'")
    assert "ZoikoGroup/zoiko-id-platform-new" in release["jobs"]["build"]["if"], (
        "forks must not run the signing build"
    )


def test_ci_uses_oidc_and_no_static_cloud_key() -> None:
    """DEVSEC-14 (configuration part): short-lived OIDC identity, no stored cloud key anywhere in CI."""
    banned = re.compile(
        r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|credentials_json|GOOGLE_CREDENTIALS|service_account_key|GCP_SA_KEY",
        re.IGNORECASE,
    )
    for p in WORKFLOWS.glob("*.yml"):
        assert not banned.search(p.read_text(encoding="utf-8")), (
            f"{p.name} references a static cloud credential"
        )
    build = _workflows()["build-image.yml"]["jobs"]["build"]
    assert build["permissions"]["id-token"] == "write"
    assert not any(
        "with" in s and "key" in str(s["with"]).lower() and "secrets." in str(s)
        for s in _steps(_workflows()["release.yml"])
    )


# ---------------------------------------------------------------- build, provenance, SBOM, signing (DEVSEC-05, 10, 25)


def test_build_pipeline_generates_provenance_sbom_and_signs_by_digest() -> None:
    """DEVSEC-05, DEVSEC-10, DEVSEC-25 (configuration): the pipeline cannot publish without the supply-chain steps."""
    wf = _workflows()["build-image.yml"]
    assert "workflow_call" in _triggers(wf), (
        "the hardened builder must be a reusable workflow (SLSA Build L3 posture)"
    )
    steps = _steps(wf)
    uses = [s.get("uses", "") for s in steps]
    assert any(u.startswith("actions/attest-build-provenance@") for u in uses)
    assert any(u.startswith("actions/attest-sbom@") for u in uses)
    runs = "\n".join(s.get("run", "") for s in steps)
    assert re.search(
        r'cosign sign --yes "\$\{IMAGE\}@\$\{\{ steps\.build\.outputs\.digest \}\}"', runs
    ), "sign by digest, never by tag"
    assert "--format cyclonedx" in runs, "CycloneDX is the primary SBOM"
    sbom = next(s for s in steps if s.get("uses", "").startswith("actions/attest-sbom@"))
    assert sbom["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}", (
        "SBOM bound to the exact digest"
    )
    assert "cosign sign" in runs and "continue-on-error" not in json.dumps(wf), (
        "signer failure must fail the build"
    )


def test_production_promotion_verifies_the_same_digest_and_never_rebuilds() -> None:
    """DEVSEC-05, DEVSEC-07, MPR-02 (configuration): promote = verify + gate on the preprod digest, behind approval."""
    wf = _workflows()["release.yml"]
    promote = wf["jobs"]["promote"]
    assert promote["environment"] == "production", "promotion needs an environment approval"
    steps = promote["steps"]
    assert not any(s.get("uses", "").startswith("docker/build-push-action") for s in steps), (
        "promotion must not rebuild"
    )
    run = "\n".join(s.get("run", "") for s in steps)
    for needle in (
        "cosign verify",
        "gh attestation verify",
        "--signer-workflow",
        "cyclonedx.org/bom",
        "release_gate.py",
        "--target prod",
        "PREPROD_DIGEST",
    ):
        assert needle in run, f"promotion step missing: {needle}"
    assert "set -euo pipefail" in run, "a failed verification must stop promotion"
    assert {"image_digest", "preprod_digest"} <= set(
        wf.get("on", wf.get(True))["workflow_dispatch"]["inputs"]
    )


# ---------------------------------------------------------------- the promotion gate (DEVSEC-07..12, 24, 25, 28, 29)


def test_a_fully_verified_candidate_is_promotable() -> None:
    d = decide(good_candidate())
    assert d.allowed, d.reasons


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda c: c.update(signature={}), "SIGNATURE_UNVERIFIED"),
        (
            lambda c: c["signature"].update(
                identity="https://github.com/evil/repo/.github/workflows/x.yml@refs/heads/main"
            ),
            "SIGNER_NOT_APPROVED",
        ),
        (
            lambda c: c["signature"].update(issuer="https://accounts.example.com"),
            "SIGNER_NOT_APPROVED",
        ),
        (lambda c: c.update(provenance={}), "PROVENANCE_UNVERIFIED"),
        (
            lambda c: c["provenance"].update(
                builder_id="https://github.com/evil/ci/.github/workflows/b.yml@refs/heads/main"
            ),
            "BUILDER_NOT_APPROVED",
        ),
        (
            lambda c: c["provenance"].update(
                source_repo="https://github.com/fork/Zoiko-ID-Platform"
            ),
            "SOURCE_NOT_APPROVED",
        ),
        (lambda c: c["provenance"].update(source_ref="refs/heads/feature"), "SOURCE_NOT_APPROVED"),
        (
            lambda c: c["provenance"].update(subject_digest="sha256:" + "b" * 64),
            "PROVENANCE_DIGEST_MISMATCH",
        ),
        (lambda c: c["provenance"].update(slsa_level=2), "SLSA_LEVEL_TOO_LOW"),
        (lambda c: c.update(sbom={}), "SBOM_MISSING"),
        (lambda c: c["sbom"].update(subject_digest="sha256:" + "c" * 64), "SBOM_DIGEST_MISMATCH"),
        (lambda c: c.update(image=IMAGE.split("@")[0] + ":latest"), "NOT_DIGEST_PINNED"),
        (lambda c: c.update(image="docker.io/library/evil@" + DIGEST), "REGISTRY_NOT_APPROVED"),
        (
            lambda c: c.update(image="ghcr.io/zoikogroup.evil.example/x@" + DIGEST),
            "REGISTRY_NOT_APPROVED",
        ),
        (lambda c: c.update(preprod_digest="sha256:" + "d" * 64), "NOT_THE_PREPROD_DIGEST"),
    ],
)
def test_gate_refuses_every_broken_supply_chain_link(mutate: Any, reason: str) -> None:
    """DEVSEC-05 (provenance/builder identity), 07 (same digest), 08/09 (signature, wrong builder/source), 10 (SBOM)."""
    c = good_candidate()
    mutate(c)
    d = decide(c)
    assert not d.allowed
    assert reason in d.reasons


@pytest.mark.parametrize(
    ("components", "reasons"),
    [
        (["event-stream@3.3.6"], ["DEPENDENCY_BLOCKED:event-stream@3.3.6"]),
        ([{"name": "Node-IPC", "version": "10.1.2"}], ["DEPENDENCY_BLOCKED:node-ipc@10.1.2"]),
        (["colourama@0.1.0"], ["DEPENDENCY_BLOCKED:colourama@0.1.0"]),
        (
            ["colors@1.4.1", "ctx@0.0.1"],
            ["DEPENDENCY_BLOCKED:colors@1.4.1", "DEPENDENCY_BLOCKED:ctx@0.0.1"],
        ),
        ([], ["SBOM_INVENTORY_MISSING"]),
        (["event-stream@4.0.1", "colors@1.4.0"], []),
    ],
)
def test_a_blocked_dependency_in_the_sbom_blocks_promotion_and_a_missing_inventory_does_too(
    components: list[Any], reasons: list[str]
) -> None:
    """SEC-24: the SBOM's components are checked against the dependency policy; no inventory means no proof.

    A listed version is blocked, other versions of the same package pass, and an unlisted version-less entry
    blocks every version. This is a deny-list: it does not discover newly malicious packages by itself."""
    c = good_candidate()
    c["sbom"]["components"] = components
    d = decide(c)
    assert d.reasons == reasons
    assert d.allowed is (not reasons)


def test_an_actively_exploited_finding_blocks_at_any_severity_and_no_exception_permits_it() -> None:
    """SEC-30 (gate part): a known-exploited CVE stops promotion even when it is only MEDIUM and even with an
    approved exception; a missing feed stops it too, because absence of the list proves nothing."""
    c = good_candidate()
    c["findings"] = [{"id": "CVE-2026-7777", "severity": "MEDIUM"}]
    assert decide(c).allowed  # medium and not exploited: allowed
    exploited = frozenset({"CVE-2026-7777"})
    assert decide(c, known_exploited=exploited).reasons == ["KNOWN_EXPLOITED_BLOCKS:CVE-2026-7777"]
    excepted = [
        {
            "finding": "CVE-2026-7777",
            "approved_by": "ciso",
            "expires": (NOW + timedelta(days=30)).isoformat(),
        }
    ]
    d = decide(c, known_exploited=exploited, exceptions=excepted)
    assert d.reasons == ["KNOWN_EXPLOITED_BLOCKS:CVE-2026-7777"]
    assert (
        "KNOWN_EXPLOITED_FEED_MISSING" in gate.evaluate(c, POLICY, target="prod", now=NOW).reasons
    )


def test_the_known_exploited_feed_is_parsed_and_the_remediation_clock_is_kept() -> None:
    """SEC-30 (workflow part): the CISA feed shape is understood; each exploited finding in a deployed image
    gets a 48-hour due time and the emergency-release path; an unrecorded first-seen time counts as overdue."""
    feed = {"vulnerabilities": [{"cveID": "CVE-2026-1"}, {"cveID": "CVE-2026-2"}]}
    assert gate.parse_known_exploited(feed) == {"CVE-2026-1", "CVE-2026-2"}
    assert gate.parse_known_exploited(["CVE-2026-3"]) == {"CVE-2026-3"}
    with pytest.raises(ValueError):
        gate.parse_known_exploited({"vulnerabilities": "nope"})
    findings = [{"id": "CVE-2026-1"}, {"id": "CVE-2026-2"}, {"id": "CVE-2026-9"}]
    first_seen = {"CVE-2026-1": (NOW - timedelta(hours=47)).isoformat()}
    plan = gate.kev_remediation(
        findings, gate.parse_known_exploited(feed), first_seen, now=NOW, policy=POLICY
    )
    assert [p["finding"] for p in plan] == [
        "CVE-2026-1",
        "CVE-2026-2",
    ]  # the unlisted one is not owed
    assert plan[0]["overdue"] is False and plan[0]["due"] == (NOW + timedelta(hours=1)).isoformat()
    assert (
        plan[1]["overdue"] is True
    )  # no first-seen record: cannot be shown to be inside the window
    late = gate.kev_remediation(
        findings,
        frozenset({"CVE-2026-1"}),
        {"CVE-2026-1": (NOW - timedelta(hours=49)).isoformat()},
        now=NOW,
        policy=POLICY,
    )
    assert late[0]["overdue"] is True and all(p["path"] == "emergency_release" for p in late)


def test_the_promotion_job_scans_every_severity_and_fetches_the_exploited_feed() -> None:
    """SEC-30: the scan keeps all severities (an exploited HIGH must reach the gate) and the feed is fetched,
    fail-closed, before the gate runs with it."""
    run = "\n".join(s.get("run", "") for s in _steps(_workflows()["release.yml"]))
    assert "--severity CRITICAL" not in run
    assert "known_exploited_vulnerabilities.json" in run and "--kev kev.json" in run
    assert run.index("known_exploited_vulnerabilities.json") < run.index("release_gate.py")
    assert POLICY["require_known_exploited_feed"] is True and POLICY["kev_remediation_hours"] <= 72


def test_registry_prefix_cannot_be_spoofed_by_a_lookalike_host() -> None:
    """DEVSEC-19 (decision part): 'ghcr.io/zoikogroup/' must match the whole path segment, not a substring."""
    c = good_candidate()
    c["image"] = "ghcr.io/zoikogroup-evil/zoiko-id-platform@" + DIGEST
    assert "REGISTRY_NOT_APPROVED" in decide(c).reasons


def test_unexcepted_critical_blocks_and_expired_exception_no_longer_permits() -> None:
    """DEVSEC-11 and DEVSEC-12."""
    c = good_candidate()
    c["findings"].append({"id": "CVE-2026-9999", "severity": "CRITICAL"})
    assert decide(c).reasons == ["VULNERABILITY_BLOCKS:CVE-2026-9999"]

    live = {
        "finding": "CVE-2026-9999",
        "approved_by": "security-lead",
        "expires": (NOW + timedelta(days=3)).isoformat(),
    }
    assert decide(c, exceptions=[live]).allowed

    expired = {**live, "expires": (NOW - timedelta(seconds=1)).isoformat()}
    assert decide(c, exceptions=[expired]).reasons == ["VULNERABILITY_BLOCKS:CVE-2026-9999"]

    anonymous = {k: v for k, v in live.items() if k != "approved_by"}
    assert not decide(c, exceptions=[anonymous]).allowed, "an exception needs a named approver"
    other = {**live, "finding": "CVE-2026-0000"}
    assert not decide(c, exceptions=[other]).allowed, "an exception covers only its own finding"


def test_signer_outage_cannot_produce_a_promotable_artifact() -> None:
    """DEVSEC-25 (gate part): when signing/verification is unavailable the record has no verified signature and is refused."""
    c = good_candidate()
    c["signature"] = {"verified": False}
    d = decide(c)
    assert not d.allowed and "SIGNATURE_UNVERIFIED" in d.reasons
    c = good_candidate()
    del c["signature"]
    assert not decide(c).allowed
    assert not gate.evaluate({}, POLICY, target="prod", now=NOW).allowed


def test_quarantined_digest_is_refused_everywhere() -> None:
    """DEVSEC-28 (gate part): a quarantined digest stops promotion even when every attestation still verifies."""
    assert decide(good_candidate(), quarantined=frozenset({DIGEST})).reasons == [
        "ARTIFACT_QUARANTINED"
    ]
    assert decide(good_candidate(), target="preprod", quarantined=frozenset({DIGEST})).reasons == [
        "ARTIFACT_QUARANTINED"
    ]


def test_release_manifest_links_commit_digest_provenance_sbom_config_and_independent_approvers() -> (
    None
):
    """DEVSEC-29: the evidence record is complete, and an author cannot be their own approver."""
    commit = "0123456789abcdef0123456789abcdef01234567"
    m = gate.release_manifest(
        good_candidate(),
        commit=commit,
        config_digest="sha256:" + "e" * 64,
        approvers=["alice", "bob", "carol"],
        change_id="CHG-1042",
        author="alice",
    )
    assert m == {
        "commit": commit,
        "image": IMAGE,
        "provenance_digest": DIGEST,
        "sbom_digest": DIGEST,
        "config_digest": "sha256:" + "e" * 64,
        "approvers": ["bob", "carol"],
        "change_id": "CHG-1042",
    }
    with pytest.raises(ValueError, match="not its author"):
        gate.release_manifest(
            good_candidate(),
            commit=commit,
            config_digest="x",
            approvers=["alice"],
            change_id="C",
            author="alice",
        )
    with pytest.raises(ValueError, match="change id"):
        gate.release_manifest(
            good_candidate(),
            commit=commit,
            config_digest="x",
            approvers=["bob"],
            change_id="",
            author="alice",
        )
    with pytest.raises(ValueError, match="40-hex"):
        gate.release_manifest(
            good_candidate(),
            commit="main",
            config_digest="x",
            approvers=["bob"],
            change_id="C",
            author="alice",
        )
    broken = good_candidate()
    del broken["sbom"]
    with pytest.raises(ValueError, match="incomplete"):
        gate.release_manifest(
            broken,
            commit=commit,
            config_digest="x",
            approvers=["bob"],
            change_id="C",
            author="alice",
        )
    assert {
        "commit",
        "image",
        "provenance_digest",
        "sbom_digest",
        "config_digest",
        "approvers",
        "change_id",
    } == set(gate.MANIFEST_FIELDS)


def test_emergency_release_needs_named_approver_change_id_review_and_still_passes_the_signature_gate() -> (
    None
):
    """DEVSEC-24 (decision part): break-glass adds duties; it never waives signing/provenance."""
    ok = {
        "approver": "bob",
        "author": "alice",
        "change_id": "EMG-7",
        "security_notified": True,
        "post_review_due": (NOW + timedelta(days=2)).isoformat(),
        "candidate": good_candidate(),
    }
    assert gate.emergency_release(ok, now=NOW, policy=POLICY, known_exploited=frozenset()).allowed
    for field, value, reason in (
        ("approver", "", "NAMED_APPROVER_MISSING"),
        ("approver", "alice", "APPROVER_IS_AUTHOR"),
        ("change_id", "", "CHANGE_ID_MISSING"),
        ("security_notified", False, "SECURITY_NOT_NOTIFIED"),
        ("post_review_due", None, "POST_REVIEW_NOT_SCHEDULED_IN_WINDOW"),
        (
            "post_review_due",
            (NOW + timedelta(days=60)).isoformat(),
            "POST_REVIEW_NOT_SCHEDULED_IN_WINDOW",
        ),
    ):
        d = gate.emergency_release(
            {**ok, field: value}, now=NOW, policy=POLICY, known_exploited=frozenset()
        )
        assert not d.allowed and reason in d.reasons, (field, d.reasons)
    unsigned = good_candidate()
    unsigned["signature"] = {}
    d = gate.emergency_release(
        {**ok, "candidate": unsigned}, now=NOW, policy=POLICY, known_exploited=frozenset()
    )
    assert not d.allowed and "SIGNATURE_UNVERIFIED" in d.reasons


def test_candidate_builder_turns_verified_outputs_into_a_gate_record_and_missing_ones_into_refusal() -> (
    None
):
    """The glue between `cosign verify` / `gh attestation verify` output and the gate fails closed."""
    sig = [
        {
            "optional": {
                "Subject": POLICY["approved_signer_identities"][0],
                "Issuer": POLICY["oidc_issuer"],
            }
        }
    ]
    att = [
        {
            "verificationResult": {
                "signature": {
                    "certificate": {
                        "buildSignerURI": BUILDER,
                        "sourceRepositoryURI": POLICY["approved_sources"][0],
                        "sourceRepositoryRef": "refs/heads/main",
                    }
                },
                "statement": {"subject": [{"digest": {"sha256": "a" * 64}}]},
            }
        }
    ]
    scan = {
        "Results": [{"Vulnerabilities": [{"VulnerabilityID": "CVE-1", "Severity": "CRITICAL"}]}]
    }
    sbom_att = [
        {
            "verificationResult": {
                "statement": {
                    "subject": [{"digest": {"sha256": "a" * 64}}],
                    "predicate": {"components": [{"name": "fastapi", "version": "0.115.0"}]},
                }
            }
        }
    ]
    built = candidate_builder.build(IMAGE, DIGEST, sig, att, sbom_att, scan)
    assert built["provenance"]["slsa_level"] == 3
    assert decide(built).reasons == ["VULNERABILITY_BLOCKS:CVE-1"]
    built = candidate_builder.build(IMAGE, DIGEST, sig, att, sbom_att, {"Results": []})
    assert decide(built).allowed
    nothing = candidate_builder.build(IMAGE, DIGEST, None, None, None, None)
    assert {"SIGNATURE_UNVERIFIED", "PROVENANCE_UNVERIFIED", "SBOM_MISSING"} <= set(
        decide(nothing).reasons
    )


def test_release_policy_names_only_the_repository_builder() -> None:
    """The gate's allowlists match the Kyverno admission policy and the real workflow file."""
    assert (WORKFLOWS / "build-image.yml").exists()
    kyv = _kyverno("verify-images.yaml")["spec"]["rules"][0]["verifyImages"][0]
    subject = kyv["attestors"][0]["entries"][0]["keyless"]["subject"]
    assert (
        subject in POLICY["approved_signer_identities"] and subject in POLICY["approved_builders"]
    )
    assert kyv["attestors"][0]["entries"][0]["keyless"]["issuer"] == POLICY["oidc_issuer"]
    assert POLICY["min_slsa_build_level"] == 3 and POLICY["blocking_severities"] == ["CRITICAL"]
    assert (
        json.loads((REPO / "deploy/release/security-exceptions.json").read_text(encoding="utf-8"))
        == []
    )


# ---------------------------------------------------------------- admission policy as code (DEVSEC-08, 09, 17, 18, 19, 26)


def test_admission_policies_enforce_and_fail_closed() -> None:
    """DEVSEC-26 (configuration): every blocking policy is Enforce with failurePolicy Fail."""
    blocking = ("verify-images.yaml", "restrict-registries.yaml", "pod-security-restricted.yaml")
    for name in blocking:
        spec = _kyverno(name)["spec"]
        assert spec["validationFailureAction"] == "Enforce", name
        assert spec["failurePolicy"] == "Fail", (
            f"{name}: unavailable admission must deny, not admit"
        )


def test_image_verification_policy_requires_signature_and_slsa_provenance_from_the_release_workflow() -> (
    None
):
    """DEVSEC-08 and DEVSEC-09 (configuration)."""
    spec = _kyverno("verify-images.yaml")["spec"]
    rule = spec["rules"][0]
    vi = rule["verifyImages"][0]
    assert vi["required"] is True and vi["verifyDigest"] is True and vi["mutateDigest"] is True
    assert vi["imageReferences"] == ["ghcr.io/zoikogroup/*"]
    keyless = vi["attestors"][0]["entries"][0]["keyless"]
    assert keyless["subject"].endswith("/.github/workflows/build-image.yml@refs/heads/main")
    att = vi["attestations"][0]
    assert att["type"] == "https://slsa.dev/provenance/v1"
    conditions = {c["key"]: c["value"] for c in att["conditions"][0]["all"]}
    assert (
        conditions["{{ buildDefinition.externalParameters.workflow.repository }}"]
        == POLICY["approved_sources"][0]
    )
    assert conditions["{{ buildDefinition.externalParameters.workflow.ref }}"] == "refs/heads/main"
    assert rule["match"]["any"][0]["resources"]["kinds"] == ["Pod"]


def test_registry_and_digest_restrictions_cover_all_container_kinds() -> None:
    """DEVSEC-19 (configuration): a name or tag that merely looks right is not enough."""
    rules = {r["name"]: r for r in _kyverno("restrict-registries.yaml")["spec"]["rules"]}
    reg = rules["approved-registry-only"]["validate"]["foreach"][0]
    assert "ephemeralContainers" in reg["list"] and "initContainers" in reg["list"]
    cond = reg["deny"]["conditions"]["all"][0]
    assert cond["operator"] == "AnyNotIn" and cond["value"] == ["ghcr.io/zoikogroup/*"]
    dig = rules["pinned-by-digest"]["validate"]["foreach"][0]["deny"]["conditions"]["all"][0]
    assert dig["operator"] == "NotEquals" and dig["value"] == "*@sha256:*"


def test_pod_security_policy_is_restricted_and_blocks_hostpath_root_and_missing_limits() -> None:
    """DEVSEC-17 (configuration)."""
    rules = {r["name"]: r for r in _kyverno("pod-security-restricted.yaml")["spec"]["rules"]}
    pss = rules["restricted-profile"]["validate"]["podSecurity"]
    assert pss["level"] == "restricted"
    assert "X(hostPath)" in rules["no-host-path"]["validate"]["pattern"]["spec"]["=(volumes)"][0]
    c = rules["read-only-root-and-limits"]["validate"]["pattern"]["spec"]["containers"][0]
    assert c["securityContext"] == {
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
    }
    assert set(c["resources"]["limits"]) == {"memory", "cpu"}
    for r in rules.values():
        sel = r["match"]["any"][0]["resources"]["namespaceSelector"]["matchLabels"]
        assert sel == {"zid.zoiko.id/protected": "true"}


def test_every_new_namespace_is_given_a_default_deny_network_policy() -> None:
    """DEVSEC-18 (configuration): generated on namespace creation, ingress and egress, kept in sync."""
    spec = _kyverno("default-deny-network.yaml")["spec"]
    rule = spec["rules"][0]
    assert rule["match"]["any"][0]["resources"]["kinds"] == ["Namespace"]
    assert "selector" not in rule["match"]["any"][0]["resources"], (
        "must apply to every namespace, not only labelled ones"
    )
    assert set(rule["exclude"]["any"][0]["resources"]["names"]) == {
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "kyverno",
    }
    gen = rule["generate"]
    assert (
        gen["kind"] == "NetworkPolicy"
        and gen["synchronize"] is True
        and spec["generateExisting"] is True
    )
    assert gen["data"]["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}


# ---------------------------------------------------------------- Helm chart golden path (DEVSEC-17, 18, 30)


def test_chart_values_satisfy_pod_security_restricted_and_promote_by_digest() -> None:
    """DEVSEC-17, DEVSEC-30 (secure baseline): the service chart passes the policies above."""
    v = _yaml(CHART / "values.yaml")
    sc, psc = v["securityContext"], v["podSecurityContext"]
    assert (
        sc["allowPrivilegeEscalation"] is False
        and sc["privileged"] is False
        and sc["readOnlyRootFilesystem"] is True
    )
    assert sc["capabilities"]["drop"] == ["ALL"]
    assert (
        psc["runAsNonRoot"] is True
        and psc["runAsUser"] > 0
        and psc["seccompProfile"]["type"] == "RuntimeDefault"
    )
    assert v["serviceAccount"]["automount"] is False and v["serviceAccount"]["name"] != "default"
    assert {"cpu", "memory"} <= set(v["resources"]["limits"])
    assert v["image"]["digest"] == "" and ":" not in v["image"]["repository"].split("/")[-1], (
        "no default digest and no tag"
    )
    assert v["image"]["repository"].startswith("ghcr.io/zoikogroup/")
    dep = (CHART / "templates/deployment.yaml").read_text(encoding="utf-8")
    assert '@{{ required "image.digest' in dep, "chart must refuse to render without a digest"
    assert 'required "environment is required"' in dep
    for ref in (
        ".Values.securityContext",
        ".Values.podSecurityContext",
        ".Values.resources",
        ".Values.serviceAccount.name",
    ):
        assert ref in dep, f"deployment template no longer applies {ref}"
    assert "hostPath" not in dep and "hostNetwork" not in dep and "privileged: true" not in dep


def test_chart_ships_default_deny_network_policy_with_explicit_allows_only() -> None:
    """DEVSEC-18 (configuration): the chart itself carries default-deny ingress and egress."""
    docs = [
        d
        for d in (CHART / "templates/networkpolicy.yaml").read_text(encoding="utf-8").split("---")
        if d.strip()
    ]
    first = yaml.safe_load(docs[0])
    assert first["spec"]["podSelector"] == {} and first["spec"]["policyTypes"] == [
        "Ingress",
        "Egress",
    ]
    assert "ingress" not in first["spec"] and "egress" not in first["spec"]
    assert "0.0.0.0/0" not in (CHART / "templates/networkpolicy.yaml").read_text(encoding="utf-8")


def test_container_image_is_nonroot_and_base_images_are_digest_pinned() -> None:
    """DEVSEC-17/30 (baseline) and DEVSEC-07: the runtime image runs as a fixed non-root uid; bases are digests."""
    text = (REPO / "backend/Dockerfile").read_text(encoding="utf-8")
    for arg in ("PYTHON_IMAGE", "UV_IMAGE"):
        assert re.search(rf"^ARG {arg}=\S+@sha256:[0-9a-f]{{64}}$", text, re.MULTILINE), (
            f"{arg} must be pinned by digest"
        )
    runtime = text.split("AS runtime", 1)[1]
    assert re.search(r"^USER zid$", runtime, re.MULTILINE) and "--uid 10001" in runtime
    assert not re.search(
        r"^(ENV|ARG)\s+\S*(PASSWORD|SECRET|TOKEN|PRIVATE_KEY)\S*=\S+",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    chart_uid = _yaml(CHART / "values.yaml")["podSecurityContext"]["runAsUser"]
    assert chart_uid == 10001, "chart uid must match the image's zid user"


# ---------------------------------------------------------------- source control (DEVSEC-03)


def test_default_and_release_branches_deny_direct_push_force_push_and_unreviewed_merge() -> None:
    """DEVSEC-03 (configuration as code; the ruleset must be applied to the GitHub organisation)."""
    rs = json.loads((REPO / ".github/rulesets/default-branch.json").read_text(encoding="utf-8"))
    assert rs["enforcement"] == "active" and rs["bypass_actors"] == []
    assert set(rs["conditions"]["ref_name"]["include"]) == {
        "~DEFAULT_BRANCH",
        "refs/heads/release/*",
    }
    rules = {r["type"]: r.get("parameters") for r in rs["rules"]}
    assert {
        "deletion",
        "non_fast_forward",
        "required_signatures",
        "pull_request",
        "required_status_checks",
    } <= set(rules)
    pr = rules["pull_request"]
    assert pr["required_approving_review_count"] >= 1 and pr["require_code_owner_review"] is True
    assert pr["require_last_push_approval"] is True and pr["dismiss_stale_reviews_on_push"] is True
    checks = {c["context"] for c in rules["required_status_checks"]["required_status_checks"]}
    jobs = {j for wf in _workflows().values() for j in wf["jobs"]}
    assert checks <= jobs, "a required check names a job that no workflow defines"
    assert {"checks", "secret-scan"} <= checks
    owners = (REPO / ".github/CODEOWNERS").read_text(encoding="utf-8")
    assert re.search(r"^/\.github/\s+@zoiko/sre @zoiko/security", owners, re.MULTILINE)
    assert re.search(r"^/deploy/\s+@zoiko/sre @zoiko/security", owners, re.MULTILINE)


# ---------------------------------------------------------------- IaC policy (DEVSEC-15)


def _change(rtype: str, after: dict[str, Any], actions: list[str] | None = None) -> dict[str, Any]:
    return {
        "address": f"{rtype}.x",
        "type": rtype,
        "change": {"actions": actions or ["create"], "after": after},
    }


@pytest.mark.parametrize(
    ("rtype", "after", "code"),
    [
        (
            "aws_security_group_rule",
            {"type": "ingress", "from_port": 22, "to_port": 22, "cidr_blocks": ["0.0.0.0/0"]},
            "PUBLIC_INGRESS",
        ),
        (
            "aws_iam_policy",
            {
                "policy": json.dumps(
                    {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}
                )
            },
            "IAM_WILDCARD",
        ),
        (
            "aws_iam_role_policy",
            {
                "policy": json.dumps(
                    {"Statement": [{"Effect": "Allow", "Action": ["s3:*"], "Resource": "*"}]}
                )
            },
            "IAM_WILDCARD",
        ),
        (
            "aws_s3_bucket_public_access_block",
            {
                "block_public_acls": True,
                "block_public_policy": False,
                "ignore_public_acls": True,
                "restrict_public_buckets": True,
            },
            "PUBLIC_BUCKET",
        ),
        (
            "aws_db_instance",
            {"storage_encrypted": False, "deletion_protection": True},
            "UNENCRYPTED_DATABASE",
        ),
        (
            "aws_db_instance",
            {"storage_encrypted": True, "publicly_accessible": True},
            "PUBLIC_DATABASE",
        ),
        ("aws_ebs_volume", {"encrypted": False}, "UNENCRYPTED_VOLUME"),
        ("aws_iam_access_key", {"user": "ci"}, "STATIC_CLOUD_KEY"),
        ("google_service_account_key", {"service_account_id": "ci"}, "STATIC_CLOUD_KEY"),
        (
            "google_project_iam_member",
            {"role": "roles/owner", "member": "user:a@example.com"},
            "IAM_WILDCARD",
        ),
        (
            "google_storage_bucket_iam_member",
            {"role": "roles/storage.objectViewer", "member": "allUsers"},
            "PUBLIC_IAM_MEMBER",
        ),
        (
            "google_compute_firewall",
            {"direction": "INGRESS", "source_ranges": ["0.0.0.0/0"]},
            "PUBLIC_INGRESS",
        ),
        (
            "google_sql_database_instance",
            {
                "settings": [
                    {
                        "ip_configuration": [
                            {"ipv4_enabled": True, "authorized_networks": [{"value": "0.0.0.0/0"}]}
                        ]
                    }
                ]
            },
            "PUBLIC_DATABASE",
        ),
        (
            "google_storage_bucket",
            {"public_access_prevention": "inherited", "location": "EU"},
            "PUBLIC_BUCKET",
        ),
        ("aws_s3_bucket", {"region": "us-east-1"}, "REGION_NOT_APPROVED"),
    ],
)
def test_iac_policy_blocks_forbidden_plan_changes(
    rtype: str, after: dict[str, Any], code: str
) -> None:
    """DEVSEC-15: public exposure, wildcard IAM, missing encryption, static keys and unapproved regions are denied."""
    violations = iac.evaluate_plan(
        {"resource_changes": [_change(rtype, after)]}, {"eu-west-2", "eu"}
    )
    assert any(v.endswith(code) for v in violations), violations


def test_iac_policy_allows_a_compliant_plan_and_ignores_deletes() -> None:
    plan = {
        "resource_changes": [
            _change(
                "aws_db_instance",
                {
                    "storage_encrypted": True,
                    "publicly_accessible": False,
                    "deletion_protection": True,
                    "region": "eu-west-2",
                },
            ),
            _change(
                "aws_security_group_rule",
                {"type": "ingress", "from_port": 443, "to_port": 443, "cidr_blocks": ["0.0.0.0/0"]},
            ),
            _change(
                "aws_iam_policy",
                {
                    "policy": json.dumps(
                        {
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["s3:GetObject"],
                                    "Resource": "arn:aws:s3:::b/*",
                                }
                            ]
                        }
                    )
                },
            ),
            _change("aws_ebs_volume", {"encrypted": False}, ["delete"]),
        ]
    }
    assert iac.evaluate_plan(plan, {"eu-west-2"}) == []


# ---------------------------------------------------------------- secrets (DEVSEC-13, MPR-15)


def test_secret_scanner_detects_planted_credentials_and_honours_the_fixture_marker() -> None:
    """DEVSEC-13 (detection part), CRYP-24: the planted secrets of each class are found; the CI incident step is asserted below."""
    planted = {
        "private-key": "-----BEGIN RSA PRIVATE KEY-----",  # zid-secret-scan: allow (synthetic fixture)
        "aws-access-key": 'key = "AKIAABCDEFGHIJKLMNOP"',  # zid-secret-scan: allow (synthetic fixture)
        "github-token": "token: ghp_" + "a" * 36,
        "stripe-live-key": "sk_live_" + "b" * 24,
        "credential-in-url": "postgresql://zid:Sup3rS3cretValue@db.internal:5432/zid",
        "gcp-service-account": '{"type": "service_account"}',  # zid-secret-scan: allow (synthetic fixture)
        "slack-token": "xoxb-1234567890-abcdefghij",  # zid-secret-scan: allow (synthetic fixture)
    }
    for name, line in planted.items():
        assert [n for _, n in scanner.scan_text(line)] == [name], name
    assert scanner.scan_text(planted["private-key"] + "  # zid-secret-scan: allow") == []
    assert (
        scanner.scan_text(
            "postgresql+asyncpg://zid:${ZID_DB_PASSWORD:-zid_local_only}@postgres:5432/zid"
        )
        == []
    )


def test_ci_scans_for_secrets_and_opens_a_rotation_incident_not_just_a_deletion() -> None:
    """DEVSEC-13 (workflow part): detection is a required check and a hit opens a revoke/rotate incident.

    The actual revocation at the provider is not performed by the workflow (no provider credentials exist in CI)."""
    wf = _workflows()["ci.yml"]
    steps = wf["jobs"]["secret-scan"]["steps"]
    assert any("secret_scan.py" in s.get("run", "") for s in steps)
    incident = next(s for s in steps if "incident" in s.get("name", "").lower())
    assert "failure()" in incident["if"] and "fork != true" in incident["if"]
    assert (
        "COMPROMISED" in incident["run"]
        and "revoke" in incident["run"]
        and "rotate" in incident["run"].lower()
    )


def test_no_confirmed_secret_or_private_key_in_tracked_source() -> None:
    """MPR-15 (source part): the repository's tracked files contain no credential-shaped string.

    Image layers, artifacts and build logs are not scanned here."""
    root = scanner.Path(REPO)
    findings = scanner.scan_paths(scanner.tracked_files(root), root)
    assert findings == [], findings


SECRET_NAME = re.compile(r"(?i)(password|secret|token|api_?key|private_?key|credential)")


def _scalars(node: Any, path: str = "") -> Any:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _scalars(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _scalars(v, f"{path}[{i}]")
    else:
        yield path, node


def test_no_long_lived_machine_credential_ships_in_images_charts_or_deploy_config() -> None:
    """MACH-22: the Dockerfile, the Helm chart and every deploy manifest carry no literal credential.

    A secret-named setting is empty, a reference (`${...}`, `*_FILE`, `secretKeyRef`) or absent. The local compose
    stack is a development harness with documented throw-away defaults; it is checked for the shape of its
    defaults, not held to the deployed standard. Image layers and the secrets broker are not scanned here."""
    dockerfile = (REPO / "backend/Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        if re.match(r"\s*(ENV|ARG)\b", line):
            assert not SECRET_NAME.search(line.split("=")[0]), line
    checked = 0
    for path in sorted((REPO / "deploy").rglob("*.y*ml")):
        if "templates" in path.parts:
            assert "kind: Secret" not in path.read_text(encoding="utf-8"), path
            continue
        for name, value in _scalars(_yaml(path)):
            if SECRET_NAME.search(name) and isinstance(value, str) and value:
                allowed = value.startswith("${") or name.endswith(("_FILE", "Ref", "_ref"))
                assert allowed, f"{path.relative_to(REPO)} {name} holds a literal"
        checked += 1
    assert checked >= 5
    compose = _yaml(REPO / "docker-compose.yml")
    for svc in compose["services"].values():
        for key, value in (svc.get("environment") or {}).items():
            if SECRET_NAME.search(key) and isinstance(value, str):
                # throw-away local defaults only: an env reference with a `_local_only` default or a stand-in
                assert value.startswith("${ZID_") or key == "KC_BOOTSTRAP_ADMIN_PASSWORD", key
    assert "local" in (REPO / "docker-compose.yml").read_text(encoding="utf-8").lower()[:600]


# ---------------------------------------------------------------- migrations: expand then contract (DEVSEC-20)

DESTRUCTIVE = re.compile(
    r"DROP\s+(TABLE|COLUMN|SCHEMA|INDEX)|drop_(table|column|index)\b|RENAME\s+(TO|COLUMN)|"
    r"ALTER\s+COLUMN\s+\w+\s+(TYPE|SET\s+NOT\s+NULL)|\.alter_column\(|TRUNCATE\s+TABLE\b|^\s*TRUNCATE\s+(?!ON\b)\w|DELETE\s+FROM",
    re.IGNORECASE | re.MULTILINE,
)


def _upgrade_sources(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    return [
        ast.get_source_segment(src, n) or ""
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    ]


def test_migrations_are_additive_unless_a_contract_step_is_explicitly_approved() -> None:
    """DEVSEC-20 (expand stage): an upgrade() may only add; destructive changes need a CONTRACT_APPROVED change id.

    Dropping/replacing a CHECK constraint is allowed (it widens); a protective BEFORE TRUNCATE trigger is not a truncate.
    Running N and N-1 application versions side by side against the expanded schema is not exercised here."""
    offenders = []
    for path in sorted((REPO / "backend/app/migrations").glob("*/versions/*.py")):
        approved = re.search(
            r'^CONTRACT_APPROVED\s*=\s*"[A-Z]+-\d+"', path.read_text(encoding="utf-8"), re.MULTILINE
        )
        for body in _upgrade_sources(path):
            if DESTRUCTIVE.search(body) and not approved:
                offenders.append(path.relative_to(REPO).as_posix())
    assert offenders == [], f"destructive upgrade without CONTRACT_APPROVED: {offenders}"


def test_the_expand_contract_lint_catches_a_destructive_upgrade() -> None:
    sample = 'def upgrade():\n    op.execute("ALTER TABLE t DROP COLUMN old")\n'
    assert DESTRUCTIVE.search(sample)
    assert not DESTRUCTIVE.search(
        'def upgrade():\n    op.execute("ALTER TABLE t ADD COLUMN new text")\n'
    )


# ---------------------------------------------------------------- feature flags cannot disable the floor (DEVSEC-23)


def test_no_setting_can_disable_the_authentication_authorization_or_evidence_floor() -> None:
    """DEVSEC-23: no Settings field names a bypass of the floor, and deployed environments refuse the dev switches."""
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    floor = re.compile(
        r"(disable|bypass|skip|no|allow_anonymous|insecure)_?(auth|authz|authorization|authn|evidence|policy|decision|signature|verify|tls)",
        re.IGNORECASE,
    )
    assert [f for f in Settings.model_fields if floor.search(f)] == []

    for override in (
        {"tenant_signup_dev_verify": True},
        {"dev_simulators": True},
        {"dev_signing_keys": {"k": "pem"}},
    ):
        with pytest.raises(RuntimeError, match="local/test only"):
            create_app(Settings(environment="prod", **override))
    with pytest.raises(RuntimeError, match="key broker"):
        create_app(Settings(environment="prod", token_issuer="https://id.example"))
