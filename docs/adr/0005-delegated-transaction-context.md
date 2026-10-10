# ADR-0005: Delegated transaction context, with no proprietary "ActAs" token

- **Status:** Proposed
- **Owner:** Identity Lead + Machine Identity Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture v2.0 ADR-05, §19.4
- **Sources:** Back-End Target Architecture §19.4; API & Interface spec §0.2, §12; AI Agent & Workload Identity spec §8, §20; System Requirements (draft protocols behind adapters)

## Context
Agents and services act on behalf of people and organizations. Every hop needs the actor, the represented principal, the mandate and the exact transaction. v1.0 proposed a public "ActAs token". Inventing a public token format would lock customers into a proprietary syntax while the IETF and OpenID standards (Transaction Tokens, Identity Chaining) are still drafts.

## Decision

### 1. Internal Transaction Context Envelope (TCE)
The TCE is an internal, versioned context object passed between trusted Zoiko enforcement components. **It is not a public bearer token and does not replace OAuth access tokens.**

```json
{
  "tce_version": 1,
  "transaction_context_id": "0199...",
  "executing_principal_id": "0199...",
  "represented_principal_id": "0199...",
  "authority_grant_id": "0199...",
  "mandate_id": "0199...",
  "action": "purchase.create",
  "resource_ref": "supplier/order",
  "purpose_id": "0199...",
  "risk_class": "R3",
  "policy_release_id": "0199...",
  "relation_revision": 882191,
  "security_revision": 184,
  "created_at": "2026-10-06T19:30:00Z",
  "expires_at": "2026-10-06T19:30:20Z",
  "commitment": "sha256:..."
}
```

**Rules:**
- Passed **by reference or as a protected internal envelope**, and bound to an audience/service.
- **Cannot extend authority** beyond the source grant or mandate.
- Contains IDs, revisions and commitments only, never raw evidence or the full authorization graph.
- Short-lived. Long-running tasks stay bound to the original mandate and re-check before each protected effect.

### 2. Mapping at protocol boundaries

| Boundary | Mapping |
|---|---|
| OAuth 2.0 resource boundary | JWT access token (RFC 9068 profile as applicable); **RFC 8693** `act` semantics; sender constraint (DPoP or mTLS); **RAR** `authorization_details` for transaction detail |
| Internal trusted call chain | TCE / transaction-token adapter behind an internal interface; customers never depend on draft syntax |
| Cross-domain (future) | Identity Chaining / Transaction Token profiles once standardized and production-ready |
| Okta cross-app access (XAA) ecosystem | ID-assertion/XAA adapter that preserves the Zoiko mandate ID and decision-evidence linkage |

### 3. Machine-actor token rules (AI Agent spec §8)
- **Client credentials:** for a service acting as itself only.
- **Token exchange (RFC 8693):** the preferred way to translate controlled delegation.
- **Client authentication:** `private_key_jwt` or mTLS client auth is preferred over shared secrets.
- **Scope:** a downstream token can never carry broader scopes or audience than the exchange policy permits.
- **Lifetime:** access-token lifetime never exceeds the validity of the source credential, session or mandate.
- **Claims:** may identify the executing workload/agent and the represented principal by opaque IDs.
- **Material represented actions:** authority remains an **online policy check**. A valid token is not authority.

## Acceptance
- No public API or SDK exposes a Zoiko-proprietary delegation token type.
- Swapping the internal TCE adapter for a standardized transaction-token profile requires no change to public business APIs (API spec acceptance).
- Agent confused-deputy tests (W4) pass: an agent can't use a TCE for a different action, resource, purpose or mandate.

## Consequences
`zid-context` carries the TCE reference. The `session` module (SES) owns the token exchange. The `decision` module validates the TCE against current revisions for R2/R3.
