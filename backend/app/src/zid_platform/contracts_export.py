"""Export published API contracts from code (ZID-API-001 §4; DEC-D4: one OpenAPI toolchain).

    python -m zid_platform.contracts_export        # rewrites every contract in CONTRACTS + ASYNCAPI_FILE

CI (`test_published_contracts_match_code`) fails if code and the committed contract drift, so a
contract change is always a reviewed diff in contracts/.

The AsyncAPI document is derived from the event catalogues and the relay's own routing rules
(`zid_outbox.lane_for`, topic `zid.<env>.<region>.<domain>.<lane>`), so it describes the channels
events are actually published on (ZID-EVENTS-001 §3-6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from zid_outbox import lane_for

REPO = Path(__file__).resolve().parents[4]
OPENAPI = REPO / "contracts" / "openapi"
# published file -> (path prefixes it covers, title)
CONTRACTS: dict[Path, tuple[tuple[str, ...], str]] = {
    OPENAPI / "authzen" / "evaluation.json": (
        ("/access/v1/",),
        "Zoiko iD AuthZEN Authorization API (OpenID AuthZEN 1.0 profile)",
    ),
    OPENAPI / "public" / "authority-mandate.json": (
        (
            "/v1/authority-grants",
            "/v1/approval-requests",
            "/v1/mandates",
            "/v1/mandate-reservations",
        ),
        "Zoiko iD Authority, Approval and Mandate API",
    ),
    OPENAPI / "public" / "oauth.json": (
        ("/oauth2/", "/.well-known/"),
        "Zoiko iD protocol endpoints: OAuth 2.0 / OpenID Connect, federation, SSF and issuer metadata",
    ),
    OPENAPI / "public" / "authentication.json": (
        ("/authn/", "/sessions", "/admin/", "/v1/step-up/"),
        "Zoiko iD Authentication, Sessions and Recovery API",
    ),
    OPENAPI / "public" / "federation.json": (
        ("/federation/",),
        "Zoiko iD Enterprise Federation API (OIDC, SAML, OpenID Federation)",
    ),
    OPENAPI / "public" / "scim.json": (
        ("/scim/v2/", "/provisioning/"),
        "Zoiko iD SCIM 2.0 Provisioning API",
    ),
    OPENAPI / "public" / "verification.json": (
        (
            "/verifications",
            "/verification-",
            "/principals/",
            "/v2/age-checks",
            "/v2/capture-alternatives",
            "/v2/providers",
            "/v2/source-of-funds",
            "/v2/travel-rule",
            "/v2/workflows",
            "/v2/self-run",
            "/v2/ownership-graphs",
            "/v2/workflow-runs",
        ),
        "Zoiko iD Identity Verification and Assurance API",
    ),
    OPENAPI / "public" / "credentials.json": (
        (
            "/presentations",
            "/credentials",
            "/credential-",
            "/relying-party-registrations",
            "/v1/credentials",
            "/v1/credential-packs",
            "/v2/capabilities",
            "/v2/coverage",
            "/v2/credentials",
            "/v2/eligibility",
            "/v2/zoikorum",
            "/v2/mandates",
            "/v2/rooms",
            "/v2/issuer-checks",
            "/v2/presentations",
            "/v2/verifications",
            "/v1/issuers",
            "/v1/eligibility",
            "/v1/consumers",
        ),
        "Zoiko iD Verifiable Credentials API (OID4VP verifier, OID4VCI issuer)",
    ),
    OPENAPI / "public" / "signals.json": (
        ("/ssf/", "/signals", "/security-state/"),
        "Zoiko iD Security Signals and Shared Signals API",
    ),
    OPENAPI / "public" / "evidence.json": (
        ("/v1/evidence/", "/v2/evidence/", "/v2/documents", "/v2/assurance", "/v2/trust-store"),
        "Zoiko iD Evidence, Audit and Provenance API (receipts, proofs, replay, export, legal holds)",
    ),
    OPENAPI / "public" / "privacy.json": (
        (
            "/v1/privacy/",
            "/v2/privacy/",
            "/v2/decisions/",
            "/v2/verification-sessions",
            "/v2/providers/",
        ),
        "Zoiko iD Privacy, Consent and Data Rights API",
    ),
    OPENAPI / "public" / "policy.json": (
        ("/v1/policy-releases",),
        "Zoiko iD Policy Administration API (releases, validation, simulation, review, publication)",
    ),
    OPENAPI / "public" / "operator-access.json": (
        (
            "/operator/",
            "/platform/",
            "/ops/",
            "/v1/lockbox-requests",
            "/v1/operator-access-policy",
            "/v1/kyb/market-policies",
            "/v1/screening/sources",
            "/v1/screening/providers",
            "/v1/screening/gates",
            "/v1/screening/releases",
            "/v1/screening/review/",
            "/v1/integrations/screening/",
        ),
        "Zoiko iD Operator Access API (JIT, customer lockbox, break-glass review)",
    ),
    OPENAPI / "public" / "residency.json": (
        (
            "/v1/cells",
            "/v1/countries",
            "/v1/offerings",
            "/v1/residency-offers",
            "/v1/tenant/residency",
            "/v1/tenant/transfer-rules",
            "/v1/tenant/cross-cell-transfers",
        ),
        "Zoiko iD Tenant Residency and Sovereignty API",
    ),
    OPENAPI / "public" / "tenant.json": (
        (
            "/v1/tenants",
            "/v1/tenant$",
            "/v1/tenant:",
            "/v1/tenant/members",
            "/v1/tenant/owners",
            "/v1/tenant/billing-",
            "/v1/tenant/transitions",
            "/v1/tenant/configuration-revisions",
            "/v1/tenant/domains",
            "/v1/approval-policy",
            "/v1/approval-cases",
            "/v1/organizations/",
            "/v1/recovery-cases",
            "/v1/containment-actions",
            "/v1/governance/",
        ),
        "Zoiko iD Tenant Lifecycle and Administration API (bootstrap, roles, owners, lifecycle, approvals)",
    ),
    OPENAPI / "public" / "onboarding.json": (
        (
            "/v1/onboarding/",
            "/v1/org-claims",
            "/v1/kyb",
            "/v1/reviews/",
            "/v1/registration/",
            "/v2/organizations",
        ),
        "Zoiko iD Tenant Onboarding API (organization gate, gates, plans, shadow, pilot, rollback)",
    ),
    OPENAPI / "public" / "integration.json": (
        ("/v1/integration/",),
        "Zoiko iD Integration API (application registry, connectors, mapping releases)",
    ),
    OPENAPI / "public" / "workload-identity.json": (
        ("/v1/machine/",),
        "Zoiko iD Machine Identity API (agents, blueprints, tools, SPIFFE bindings, environment quarantine)",
    ),
    OPENAPI / "public" / "access-readiness.json": (
        ("/v1/access-readiness",),
        "Zoiko iD Access Readiness Agent API (setup, dry run, release approval, runs, evidence, review proposals; default off)",
    ),
    OPENAPI / "public" / "webhooks.json": (
        ("/v1/webhooks/", "/v2/webhook-endpoints", "/v2/events"),
        "Zoiko iD Webhook API (endpoints, verification, deliveries, replay)",
    ),
    OPENAPI / "public" / "notification.json": (
        ("/v1/alerts",),
        "Zoiko iD Alerts and Tasks API (inbox, acknowledge, resolve)",
    ),
    OPENAPI / "public" / "search.json": (
        ("/v1/search",),
        "Zoiko iD Global Search API (authorization-aware, tenant-scoped)",
    ),
    OPENAPI / "public" / "settings.json": (
        ("/v1/settings",),
        "Zoiko iD Tenant Settings API (authentication limits, notifications, branding, cryptography overview)",
    ),
    OPENAPI / "public" / "commercial.json": (
        (
            "/v1/catalog",
            "/v1/billing/estimate",
            "/v1/commercial/",
            "/v1/tenants/{tenant_id}/entitlements",
        ),
        "Zoiko iD Commercial API (catalogue, plans, entitlements, price books)",
    ),
    OPENAPI / "public" / "communication.json": (
        ("/v1/tenant/contacts", "/v1/account/contact", "/email/"),
        "Zoiko iD Communication API (email contacts and the one-click unsubscribe)",
    ),
    OPENAPI / "public" / "usage.json": (
        ("/v1/usage",),
        "Zoiko iD API Usage and Rate Limit API",
    ),
}
AUTHZEN_FILE = OPENAPI / "authzen" / "evaluation.json"


def _refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def contract(prefixes: tuple[str, ...], title: str) -> dict[str, Any]:
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    spec = create_app(Settings(database_url=None)).openapi()
    exact = {x[:-1] for x in prefixes if x.endswith("$")}  # "/v1/tenant$": that path only
    starts = tuple(x for x in prefixes if not x.endswith("$"))
    paths = {p: v for p, v in spec["paths"].items() if p in exact or p.startswith(starts)}
    schemas = spec.get("components", {}).get("schemas", {})
    used: set[str] = set()
    _refs(paths, used)
    pending = set(used)
    while pending:  # close over schemas referenced by schemas
        found: set[str] = set()
        _refs([schemas[n] for n in pending], found)
        pending = found - used
        used |= found
    return {
        "openapi": spec["openapi"],
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": {n: schemas[n] for n in sorted(used)}},
    }


def authzen_contract() -> dict[str, Any]:
    return contract(*CONTRACTS[AUTHZEN_FILE])


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


ASYNCAPI = REPO / "contracts" / "asyncapi"
ASYNCAPI_FILE = ASYNCAPI / "channels" / "zoiko-id.asyncapi.json"
CLOUDEVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "CloudEvents 1.0 structured envelope with Zoiko iD extensions (ZID-EVENTS-001 §4).",
    "required": [
        "specversion",
        "id",
        "type",
        "source",
        "subject",
        "time",
        "tenantid",
        "cellid",
        "aggregatetype",
        "aggregateid",
        "aggregateversion",
        "correlationid",
        "data",
    ],
    "properties": {
        "specversion": {"const": "1.0"},
        "id": {"type": "string", "description": "UUIDv7; stable across retries and replays"},
        "type": {"type": "string", "pattern": r"^com\.zoiko\.id\.[a-z0-9_.]+\.v[0-9]+$"},
        "source": {"type": "string"},
        "subject": {"type": "string"},
        "time": {"type": "string", "format": "date-time"},
        "datacontenttype": {"const": "application/json"},
        "dataschema": {"type": "string"},
        "tenantid": {"type": "string", "format": "uuid"},
        "cellid": {"type": "string"},
        "aggregatetype": {"type": "string"},
        "aggregateid": {"type": "string"},
        "aggregateversion": {
            "type": "integer",
            "minimum": 1,
            "description": "ordering key per aggregate",
        },
        "correlationid": {"type": "string"},
        "causationid": {"type": ["string", "null"]},
        "classification": {"type": "string"},
        "data": {
            "type": "object",
            "description": "identifiers, versions and amounts only; no PII payloads",
        },
    },
}


def asyncapi_contract() -> dict[str, Any]:
    events: dict[str, dict[str, Any]] = {}
    for file, status in (
        ("event-catalogue.yaml", "catalogued"),
        ("proposed-events.yaml", "proposed"),
    ):
        for name, e in (yaml.safe_load((ASYNCAPI / file).read_text(encoding="utf-8")) or {})[
            "events"
        ].items():
            events[name] = {**e, "_status": status}
    channels: dict[str, Any] = {}
    messages: dict[str, Any] = {}
    for name in sorted(events):
        e = events[name]
        domain, lane = name.split(".")[0], lane_for(name)
        key = f"{domain}.{lane}"
        ch = channels.setdefault(
            key,
            {
                "address": f"zid.{{env}}.{{region}}.{domain}.{lane}",
                "parameters": {
                    "env": {"description": "deployment environment"},
                    "region": {"description": "home region of the publishing cell"},
                },
                "messages": {},
            },
        )
        mid = name.replace(".", "_")
        ch["messages"][mid] = {"$ref": f"#/components/messages/{mid}"}
        messages[mid] = {
            "name": e["cloudevents_type"],
            "title": name,
            "summary": e.get("meaning", ""),
            "contentType": "application/cloudevents+json",
            "payload": {"$ref": "#/components/schemas/CloudEvent"},
            "x-zid-source": e.get("source", ""),
            "x-zid-status": e["_status"],
        }
    operations = {
        f"publish_{k.replace('.', '_')}": {
            "action": "send",
            "channel": {"$ref": f"#/channels/{k}"},
            "summary": "Outbox relay; Kafka key = aggregatetype:aggregateid",
        }
        for k in sorted(channels)
    }
    return {
        "asyncapi": "3.0.0",
        "info": {
            "title": "Zoiko iD integration events",
            "version": "0.1.0",
            "description": "At-least-once delivery from the transactional outbox; consumers dedupe by id.",
        },
        "channels": channels,
        "operations": operations,
        "components": {"messages": messages, "schemas": {"CloudEvent": CLOUDEVENT_SCHEMA}},
    }


def main() -> None:
    ASYNCAPI_FILE.parent.mkdir(parents=True, exist_ok=True)
    ASYNCAPI_FILE.write_text(render(asyncapi_contract()), encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {ASYNCAPI_FILE}\n")
    for path, (prefixes, title) in CONTRACTS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(contract(prefixes, title)), encoding="utf-8", newline="\n")
        sys.stdout.write(f"wrote {path}\n")


if __name__ == "__main__":
    main()
