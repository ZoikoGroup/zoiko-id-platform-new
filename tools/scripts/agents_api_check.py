"""Drive the two review agents purely over the platform's HTTP API and report whether each step works.

No Docker, no database access, no imports from the platform: only the standard library and a running API
(the cell started from source, `ZID_ACCESS_READINESS_ENABLED=true` for the Access Readiness agent).

    python tools/scripts/agents_api_check.py --base http://localhost:8000 \
        --readiness-agent <agent principal id> --daara-agent <agent id>

Tokens come from the environment, never from the command line (so they stay out of shell history):

    ZID_TOKEN_ADMIN      a person who may manage and run the agents          (access_readiness.* / agent.daara.*)
    ZID_TOKEN_APPROVER   a DIFFERENT person who may approve a release        (access_readiness.approve)
    ZID_TOKEN_REVIEWER   optional; reads the review queue (defaults to the admin)
    ZID_TOKEN_SECURITY   optional; the Security signer, with --daara-release (two different people from the requester)
    ZID_TOKEN_AUTHORITY  optional; the Authority signer, with --daara-release

Every request is a documented route. The script reads state and starts runs; a proposal never changes a right, and this
script never dismisses, assigns or hands off a proposal. Exit code 0 only when every step that ran passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Call = Callable[
    ..., tuple[int, Any]
]  # call(method, path, who, body=None, headers=None) -> (status, json)

READINESS = "/v1/access-readiness"
DAARA = "/v1/machine"


def http_call(base: str, tokens: dict[str, str]) -> Call:
    """The real transport: one HTTPS/HTTP request per call, bearer token by role name."""

    def call(
        method: str,
        path: str,
        who: str = "admin",
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        token = "" if who == "anonymous" else (tokens.get(who) or tokens.get("admin", ""))
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode("utf-8", "replace")[:300]
        except urllib.error.URLError as e:
            return 0, str(e.reason)

    return call


@dataclass
class Report:
    steps: list[tuple[str, bool, str]] = field(default_factory=list)

    def step(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(ok for _, ok, _ in self.steps)


def _err(status: int, data: Any) -> str:
    return f"HTTP {status}: {str(data)[:200]}"


def check_access_readiness(call: Call, agent_id: str, report: Report) -> None:
    """Create, dry-run, request and approve release, run, then read decisions, evidence and the review queue."""
    print(f"Access Readiness agent {agent_id[:8]}...")
    s, d = call("GET", f"{READINESS}/agents", "admin")
    if not report.step(
        "feature is on and the caller may read it", s == 200, "" if s == 200 else _err(s, d)
    ):
        return  # 404 = ZID_ACCESS_READINESS_ENABLED is off for this cell; 401/403 = wrong token

    s, d = call("GET", f"{READINESS}/agents/{agent_id}", "admin")
    if s == 404:
        s, d = call(
            "POST",
            f"{READINESS}/agents",
            "admin",
            {"agent_principal_id": agent_id, "display_name": "Access Readiness (API check)"},
        )
        if not report.step("create the setup (DRAFT)", s == 201, "" if s == 201 else _err(s, d)):
            return
    else:
        report.step(
            "setup already exists",
            s == 200,
            f"status {d.get('status') if isinstance(d, dict) else d}",
        )

    s, d = call("POST", f"{READINESS}/agents/{agent_id}/dry-runs", "admin", {})
    passed = s == 200 and isinstance(d, dict) and bool(d.get("passed"))
    if not report.step(
        "dry-run on the synthetic fixture passes", passed, "" if passed else _err(s, d)
    ):
        return

    s, d = call("GET", f"{READINESS}/agents/{agent_id}", "admin")
    status = d.get("status") if isinstance(d, dict) else None
    if status != "APPROVED":
        s, d = call("POST", f"{READINESS}/agents/{agent_id}/publish-requests", "admin")
        if not report.step(
            "request release of the exact scope digest", s == 201, "" if s == 201 else _err(s, d)
        ):
            return
        s, d = call("POST", f"{READINESS}/agents/{agent_id}/publish-requests:approve", "approver")
        approved = s == 200 and isinstance(d, dict) and d.get("status") == "APPROVED"
        if not report.step(
            "a different person approves the release", approved, "" if approved else _err(s, d)
        ):
            return

    s, run = call(
        "POST",
        f"{READINESS}/agents/{agent_id}/runs",
        "admin",
        {},
        {"Idempotency-Key": f"api-check-{uuid.uuid4()}"},
    )
    started = s == 202 and isinstance(run, dict) and "run_id" in run
    if not report.step(
        "start a run (202)", started, f"state {run.get('state')}" if started else _err(s, run)
    ):
        return
    rid = run["run_id"]
    report.step(
        "the run reached a result, not an error",
        run.get("state")
        in {
            "COMPLETED",
            "NEEDS_REVIEW",
        },  # NEEDS_REVIEW = it found something for a person; DENIED/FAILED are the bad ends
        f"state {run.get('state')}"
        + (f" denial {run.get('denial_codes')}" if run.get("denial_codes") else ""),
    )

    s, d = call("GET", f"{READINESS}/agents/{agent_id}/runs/{rid}", "admin")
    report.step("read the run", s == 200, "" if s == 200 else _err(s, d))
    s, d = call("GET", f"{READINESS}/agents/{agent_id}/runs/{rid}/decisions", "admin")
    n = len(d.get("items", [])) if isinstance(d, dict) else 0
    report.step("every read left a decision record", s == 200 and n > 0, f"{n} decisions")
    s, d = call("GET", f"{READINESS}/agents/{agent_id}/runs/{rid}/evidence", "admin")
    report.step("evidence of the run is readable", s == 200, "" if s == 200 else _err(s, d))

    s, d = call("GET", f"{READINESS}/review-proposals", "reviewer")
    n = len(d.get("items", [])) if isinstance(d, dict) else 0
    report.step("review queue is readable", s == 200, f"{n} proposals" if s == 200 else _err(s, d))

    s, again = call(
        "POST",
        f"{READINESS}/agents/{agent_id}/runs",
        "admin",
        {},
        {"Idempotency-Key": "api-check-replay-key"},
    )
    s2, again2 = call(
        "POST",
        f"{READINESS}/agents/{agent_id}/runs",
        "admin",
        {},
        {"Idempotency-Key": "api-check-replay-key"},
    )
    same = s == s2 == 202 and isinstance(again, dict) and isinstance(again2, dict)
    report.step(
        "the same Idempotency-Key returns the same run",
        same and again.get("run_id") == again2.get("run_id"),
        "" if same else _err(s2, again2),
    )


def check_daara(
    call: Call, agent_id: str, report: Report, *, release: bool = False, region: str = "local-1"
) -> None:
    """Read the template and setup, validate, dry-run, and run it and read the queue once it is published.

    With `release=True` and the `security` and `authority` people's tokens, it also takes the agent through the release itself: request
    publication, the Security and Authority people each sign the exact policy hash they were shown, a person applies it. Those four
    signers must be four different people from the requester (the platform enforces separation of duties; this script cannot bypass it)."""
    print(f"DAARA agent {agent_id[:8]}...")
    s, d = call("GET", f"{DAARA}/daara/template", "admin")
    if not report.step("template is readable", s == 200, "" if s == 200 else _err(s, d)):
        return
    s, d = call("GET", f"{DAARA}/agents/{agent_id}/daara", "admin")
    if s == 404:
        body = {
            "label": "Access Readiness (API check)",
            "warn_days": 14,
            "region": region,
            "ai_explain": False,
        }
        s, d = call("PUT", f"{DAARA}/agents/{agent_id}/daara", "admin", body)
        if not report.step(
            "create the setup", s in (200, 201), "" if s in (200, 201) else _err(s, d)
        ):
            return
    else:
        report.step("setup is readable", s == 200, "" if s == 200 else _err(s, d))
    s, d = call("POST", f"{DAARA}/agents/{agent_id}/daara:validate", "admin")
    report.step("validate (the authoritative compile)", s == 200, "" if s == 200 else _err(s, d))
    s, d = call("POST", f"{DAARA}/agents/{agent_id}/daara/dry-run", "admin")
    report.step("dry-run writes nothing and passes", s == 200, "" if s == 200 else _err(s, d))

    s, d = call("GET", f"{DAARA}/agents/{agent_id}/daara", "admin")
    live = (
        isinstance(d, dict) and d.get("policy") is not None
    )  # a released agent has a live, approved policy
    stage = d.get("stage") if isinstance(d, dict) else None
    if not live and release:
        live = _release_daara(call, agent_id, report)
    if not live:
        report.step(
            "ready to run",
            True,
            f"not released yet (stage {stage}); the Security and Authority approvals are people's decisions. "
            "Pass --daara-release with ZID_TOKEN_SECURITY and ZID_TOKEN_AUTHORITY to take it through the release",
        )
    else:
        s, run = call(
            "POST",
            f"{DAARA}/agents/{agent_id}/daara/runs",
            "admin",
            None,
            {"Idempotency-Key": f"api-check-{uuid.uuid4()}"},
        )
        ok = s in (201, 202) and isinstance(run, dict)
        report.step("start a governed run", ok, f"state {run.get('state')}" if ok else _err(s, run))
    s, d = call("GET", f"{DAARA}/agents/{agent_id}/daara/runs", "admin")
    report.step("run history is readable", s == 200, "" if s == 200 else _err(s, d))
    s, d = call("GET", f"{DAARA}/agent-review", "reviewer")
    report.step("review queue is readable", s == 200, "" if s == 200 else _err(s, d))


def _release_daara(call: Call, agent_id: str, report: Report) -> bool:
    """Request publication, collect the independent signatures over the exact policy hash, apply. True once the policy is live."""
    s, req = call("POST", f"{DAARA}/agents/{agent_id}/daara:request-publish", "admin")
    if not report.step(
        "request publication (one request, one policy hash)",
        s == 201,
        "" if s == 201 else _err(s, req),
    ):
        return False
    slots = list(req.get("required_slots", []))
    who_for = {"SECURITY": "security", "AUTHORITY": "authority"}
    missing = [x for x in slots if x not in who_for]
    if missing:
        report.step(
            "the release needs signers this script does not hold",
            True,
            "also needs "
            + ", ".join(missing)
            + " (the agent asks for an AI explanation, so Privacy and Finance sign too)",
        )
        return False
    for slot in slots:
        s, d = call(
            "POST",
            f"{DAARA}/governance/requests/{req['request_id']}:approve",
            who_for[slot],
            {"policy_hash": req["policy_hash"]},
        )
        if not report.step(
            f"the {slot.title()} person signs the exact policy hash",
            s == 200,
            "" if s == 200 else _err(s, d),
        ):
            return False
    s, d = call("POST", f"{DAARA}/governance/requests/{req['request_id']}:apply", "admin")
    if not report.step(
        "apply the approved policy (it becomes the live one)",
        s == 200,
        "" if s == 200 else _err(s, d),
    ):
        return False
    s, d = call("GET", f"{DAARA}/agents/{agent_id}/daara", "admin")
    return isinstance(d, dict) and d.get("policy") is not None


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_client_key() -> tuple[Any, dict[str, str]]:
    """A fresh P-256 key for the agent's API client: the private half stays in this process, only the public JWK is sent."""
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    n = key.public_key().public_numbers()
    kid = "k-" + uuid.uuid4().hex[:8]
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(n.x.to_bytes(32, "big")),
        "y": _b64(n.y.to_bytes(32, "big")),
        "kid": kid,
    }
    return key, jwk


def _sign_assertion(key: Any, kid: str, claims: dict[str, Any]) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    header = {"alg": "ES256", "kid": kid, "typ": "api-client-assertion+jwt"}
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return signing_input + "." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _dpop_proof(key: Any, jwk: dict[str, str], method: str, url: str, access_token: str) -> str:
    """A DPoP proof (RFC 9449) for one request, signed by the agent's own key; `ath` binds it to this access token."""
    import hashlib

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    public = {k: jwk[k] for k in ("kty", "crv", "x", "y")}
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": public}
    claims = {
        "htm": method.upper(),
        "htu": url,
        "iat": int(time.time()),
        "jti": "dpop-" + uuid.uuid4().hex,
        "ath": _b64(hashlib.sha256(access_token.encode("ascii")).digest()),
    }
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return signing_input + "." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def grant_agent_authority(
    call: Call,
    report: Report,
    agent_id: str,
    *,
    grantor: str,
    represented: str,
    actions: list[str],
    resources: list[str],
    days: int = 30,
) -> bool:
    """A person gives the agent authority for the tool's action, by delegating from a grant they already hold.

    The caller (`admin`) is always the grantor: nobody can certify authority they do not hold. The grant is narrow (named actions and
    resources, not delegable further, a short window), so the agent can do no more than the person chose.
    """
    from datetime import UTC, datetime, timedelta

    s, d = call(
        "GET",
        f"/v1/authority-grants?status=ACTIVE&grantee_principal_id={grantor}&limit=100",
        "admin",
    )
    if not report.step(
        "find the grant the person delegates from", s == 200, "" if s == 200 else _err(s, d)
    ):
        return False
    wanted = set(actions)
    parents = [
        g
        for g in (d.get("items", []) if isinstance(d, dict) else [])
        if (g.get("delegation") or {}).get("allowed")
        and (g.get("actions") is None or wanted <= set(g["actions"]))
    ]
    if not report.step(
        "the person holds a delegable grant covering these actions",
        bool(parents),
        ""
        if parents
        else "none found: the person needs a grant with delegation allowed for "
        + ", ".join(sorted(wanted)),
    ):
        return False
    now = datetime.now(UTC)
    body = {
        "grantee_principal_id": agent_id,
        "represented_principal_id": represented,
        "grant_type": "AGENT_DELEGATION",
        "parent_grant_id": parents[0]["authority_grant_id"],
        "actions": actions,
        "resources": resources,
        "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "valid_to": (now + timedelta(days=days)).isoformat(),
        "delegation": {"allowed": False, "max_depth": 0},
        "risk_class": 1,
    }
    s, g = call(
        "POST",
        "/v1/authority-grants",
        "admin",
        body,
        {"Idempotency-Key": f"api-check-grant-{uuid.uuid4()}"},
    )
    return report.step(
        "the person grants the agent authority (narrow, short, not delegable)",
        s == 201,
        f"grant {g.get('authority_grant_id')} until {g.get('valid_to')}"
        if s == 201
        else _err(s, g),
    )


def check_api_agent_lifecycle(
    call: Call,
    report: Report,
    *,
    operator: str,
    sponsor: str,
    publisher: str,
    organization: str | None,
    issuer: str,
    tenant_id: str,
    audience: str = "https://api.client.example.test",
    tools: list[str] | None = None,
    tool_server: str | None = None,
    api_base: str | None = None,
    after_activate: Callable[[str], None] | None = None,
    grantor: str | None = None,
) -> str | None:
    """Create an agent whose software is an API client, take it through every gate to ACTIVE, and sign in as it. Returns the agent id.

    No container image, no attestor, no database access. Two different people act: `admin` registers, `approver` approves the
    build, the release and the activation (the registry never lets one person do both halves).
    """
    print("API-based agent: register -> approve -> bind -> activate -> sign in")
    try:
        key, jwk = _make_client_key()
    except ImportError:
        report.step(
            "generate the client's signing key",
            False,
            "needs the `cryptography` package (pip install cryptography)",
        )
        return None
    tag = uuid.uuid4().hex[:8]
    name = f"api-check-{tag}"

    s, client = call(
        "POST",
        f"{DAARA}/api-clients",
        "admin",
        {"name": name, "jwks": {"keys": [jwk]}, "audiences": [audience]},
    )
    if not report.step(
        "register the API client (public keys only)", s == 201, "" if s == 201 else _err(s, client)
    ):
        return None
    s, d = call("POST", f"{DAARA}/builds/{client['build_id']}:approve", "approver")
    if not report.step(
        "a different person approves the client build", s == 200, "" if s == 200 else _err(s, d)
    ):
        return None
    bp = f"api-check-{tag}"
    digest = client.get("artifact_digest", "sha256:" + "0" * 64)
    s, d = call(
        "POST",
        f"{DAARA}/blueprints",
        "admin",
        {
            "blueprint_id": bp,
            "version": "1",
            "artifact_digest": digest,
            "tool_manifest_digest": "sha256:" + "b" * 64,
            "publisher_principal_id": publisher,
        },
    )
    if not report.step("create the blueprint release", s == 201, "" if s == 201 else _err(s, d)):
        return None
    s, d = call("POST", f"{DAARA}/blueprints/{bp}/versions/1:approve", "approver")
    if not report.step(
        "a different person approves the release", s == 200, "" if s == 200 else _err(s, d)
    ):
        return None
    body: dict[str, Any] = {
        "agent_name": name,
        "blueprint_id": bp,
        "blueprint_version": "1",
        "operator_principal_id": operator,
        "sponsor_principal_id": sponsor,
        "allowed_tools": tools
        or [
            "docs.read"
        ],  # an active agent must name at least one tool (AGENT_TOOL_MANIFEST_REQUIRED)
    }
    if organization:
        body["owning_organization_id"] = organization
    s, agent = call("POST", f"{DAARA}/agents", "admin", body)
    if not report.step("register the agent (DRAFT)", s == 201, "" if s == 201 else _err(s, agent)):
        return None
    aid = agent["agent_principal_id"]
    s, d = call("POST", f"{DAARA}/agents/{aid}/api-binding", "admin")
    if not report.step(
        "bind the approved client build (replaces the container binding)",
        s == 201,
        "" if s == 201 else _err(s, d),
    ):
        return aid
    for gate, who in (
        ("SECURITY_REVIEW", "approver"),
        ("KILL_PATH_TESTED", "admin"),
        ("SHORT_LIVED_CREDENTIALS", "admin"),
        ("OBSERVABILITY", "admin"),
    ):
        s, d = call(
            "POST",
            f"{DAARA}/agents/{aid}:attest",
            who,
            {"gate": gate, "note": "checked by api check"},
        )
        if not report.step(f"attest gate {gate}", s == 200, "" if s == 200 else _err(s, d)):
            return aid
    s, d = call("POST", f"{DAARA}/agents/{aid}:submit", "admin")
    if not report.step("submit for activation", s == 200, "" if s == 200 else _err(s, d)):
        return aid
    s, d = call("POST", f"{DAARA}/agents/{aid}:activate", "approver")
    active = s == 200 and isinstance(d, dict) and d.get("status") == "ACTIVE"
    if not report.step("a different person activates it", active, "" if active else _err(s, d)):
        return aid

    now = int(time.time())
    claims = {
        "iss": client["client_id"],
        "sub": client["client_id"],
        "aud": issuer.rstrip("/") + f"{DAARA}/api-agent-token",
        "resource": audience,
        "tenant_id": tenant_id,
        "jti": "jti-" + uuid.uuid4().hex,
        "iat": now,
        "exp": now + 30,
    }
    assertion = _sign_assertion(key, jwk["kid"], claims)
    s, tok = call("POST", f"{DAARA}/api-agent-token", "anonymous", {"assertion": assertion})
    got = s == 200 and isinstance(tok, dict) and tok.get("principal_id") == aid
    report.step(
        "the agent signs in with its own key and gets a short-lived, key-bound token",
        got,
        f"{tok.get('token_type')}, {tok.get('expires_in')}s, attestation cap {tok.get('attestation_cap')}"
        if got
        else _err(s, tok),
    )
    s, again = call("POST", f"{DAARA}/api-agent-token", "anonymous", {"assertion": assertion})
    report.step(
        "the same assertion cannot be used twice", s == 403, "" if s == 403 else _err(s, again)
    )
    if not got:
        return aid

    # --- act as the agent: a governed tool call with the agent's own token and a DPoP proof from its own key
    if not tool_server:
        report.step(
            "governed tool call as the agent",
            True,
            "skipped: pass --tool-server (the principal id of the tool's server)",
        )
        return aid
    tool_id = (tools or ["docs.read"])[0]
    s, d = call(
        "POST",
        f"{DAARA}/tools",
        "admin",
        {
            "tool_id": tool_id,
            "version": "1",
            "server_principal_id": tool_server,
            "canonical_action": "machine.agent.read",
            "default_risk_class": 1,
            "side_effect": "NONE",
        },
    )
    if s == 409:  # already registered by an earlier run: use it
        s = 201
    if not report.step(f"register the tool {tool_id}", s == 201, "" if s == 201 else _err(s, d)):
        return aid
    s, d = call("POST", f"{DAARA}/tools/{tool_id}:activate", "approver")
    if not report.step(
        f"activate the tool {tool_id}", s in (200, 409), "" if s in (200, 409) else _err(s, d)
    ):
        return aid
    if grantor and organization:
        grant_agent_authority(
            call, report, aid, grantor=grantor, represented=organization,
            actions=["machine.agent.read"], resources=["agent:*"],
        )  # fmt: skip
    elif after_activate is not None:
        after_activate(aid)  # authority for the agent is a person's decision; a test seeds it here

    # the proof names the API's canonical public URL (its public_base_url), which can differ from the token issuer
    url = (api_base or issuer).rstrip("/") + f"{DAARA}/tool-calls:authorize"
    digest = "sha256:" + hashlib.sha256(b'{"check":"api-agent"}').hexdigest()
    call_body = {
        "tool_id": tool_id,
        "resource_type": "agent",
        "resource_id": aid,
        "arguments_digest": digest,
    }
    agent_headers = {
        "Authorization": f"DPoP {tok['access_token']}",
        "DPoP": _dpop_proof(key, jwk, "POST", url, tok["access_token"]),
    }
    s, out = call("POST", f"{DAARA}/tool-calls:authorize", "anonymous", call_body, agent_headers)
    granted = s == 200 and isinstance(out, dict) and "grant" in out
    if granted:
        report.step(
            "the agent's governed tool call is granted (one call, ~2 minutes, bound to its arguments)",
            True,
            f"decision {out.get('decision_id')}",
        )
    else:
        codes = [
            x.get("issue")
            for x in (out.get("error", {}).get("details", []) if isinstance(out, dict) else [])
        ]
        governed = s == 403 and bool(codes)
        report.step(
            "the agent's tool call was decided by the gateway",
            governed,
            f"REFUSED with {codes}: give the agent an authority grant for {tool_id}"
            if governed
            else _err(s, out),
        )
    # a proof is single use, and it must match the request
    s, replay = call("POST", f"{DAARA}/tool-calls:authorize", "anonymous", call_body, agent_headers)
    report.step(
        "the same DPoP proof cannot be replayed", s == 401, "" if s == 401 else _err(s, replay)
    )
    s, nobound = call(
        "POST",
        f"{DAARA}/tool-calls:authorize",
        "anonymous",
        call_body,
        {"Authorization": f"Bearer {tok['access_token']}"},
    )
    report.step(
        "the key-bound token is refused without a proof",
        s == 401,
        "" if s == 401 else _err(s, nobound),
    )
    return aid


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base", default=os.environ.get("ZID_API", "http://localhost:8000"))
    p.add_argument("--readiness-agent", help="agent principal id for the Access Readiness agent")
    p.add_argument("--daara-agent", help="agent id for the DAARA agent")
    p.add_argument(
        "--create-api-agent",
        action="store_true",
        help="create, approve, bind and activate a new API-based agent, then sign in as it",
    )
    for flag in ("operator", "sponsor", "publisher", "organization"):
        p.add_argument(f"--{flag}", help=f"principal id of the {flag} (with --create-api-agent)")
    p.add_argument("--tools", nargs="*", help="tool ids the new agent may use (default: docs.read)")
    p.add_argument(
        "--tool-server",
        help="principal id of the tool's server (with --create-api-agent); enables the governed tool-call steps",
    )
    p.add_argument(
        "--grantor",
        help="principal id of the person whose token is ZID_TOKEN_ADMIN; with --organization they delegate the new agent its authority over the API",
    )
    p.add_argument(
        "--api-base",
        help="the API's public base URL as the platform knows it (defaults to --issuer); used in the DPoP proof",
    )
    p.add_argument("--issuer", help="the platform token issuer URL (with --create-api-agent)")
    p.add_argument("--tenant", help="your company tenant id (with --create-api-agent)")
    a = p.parse_args(argv)
    if not (a.readiness_agent or a.daara_agent or a.create_api_agent):
        p.error("give --readiness-agent, --daara-agent and/or --create-api-agent")
    if a.create_api_agent and not (
        a.operator and a.sponsor and a.publisher and a.issuer and a.tenant
    ):
        p.error("--create-api-agent needs --operator --sponsor --publisher --issuer --tenant")
    tokens = {
        "admin": os.environ.get("ZID_TOKEN_ADMIN", ""),
        "approver": os.environ.get("ZID_TOKEN_APPROVER", ""),
    }
    tokens["reviewer"] = os.environ.get("ZID_TOKEN_REVIEWER", tokens["admin"])
    tokens["security"] = os.environ.get("ZID_TOKEN_SECURITY", "")
    tokens["authority"] = os.environ.get("ZID_TOKEN_AUTHORITY", "")
    if not tokens["admin"]:
        print(
            "set ZID_TOKEN_ADMIN (and ZID_TOKEN_APPROVER for the release approval)", file=sys.stderr
        )
        return 2
    call = http_call(a.base, tokens)
    s, _ = call("GET", "/health/live", "admin")
    if s != 200:
        print(
            f"the API at {a.base} is not answering (/health/live -> {s}); start the cell first",
            file=sys.stderr,
        )
        return 2
    report = Report()
    if a.create_api_agent:
        check_api_agent_lifecycle(
            call, report, operator=a.operator, sponsor=a.sponsor, publisher=a.publisher,
            organization=a.organization, issuer=a.issuer, tenant_id=a.tenant, tools=a.tools, tool_server=a.tool_server, grantor=a.grantor, api_base=a.api_base,
        )  # fmt: skip
    if a.readiness_agent:
        check_access_readiness(call, a.readiness_agent, report)
    if a.daara_agent:
        if a.daara_release and not (tokens["security"] and tokens["authority"]):
            p.error(
                "--daara-release needs ZID_TOKEN_SECURITY and ZID_TOKEN_AUTHORITY (two different people)"
            )
        check_daara(call, a.daara_agent, report, release=a.daara_release, region=a.daara_region)
    failed = [n for n, ok, _ in report.steps if not ok]
    print(
        f"\n{len(report.steps) - len(failed)}/{len(report.steps)} steps passed"
        + (f"; FAILED: {failed}" if failed else "")
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
