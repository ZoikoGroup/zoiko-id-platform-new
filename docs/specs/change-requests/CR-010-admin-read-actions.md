# CR-010: Administrative read actions and mandate creation

- **Target:** action catalogue (DEC-B1); ZID-API-001 §10-11; ZID-MANDATE-001 §9-10 · **Raised by:** web admin console (tenant administration reads) · **Status:** Open

The tenant admin console lists grants, approvals, mandates, credentials, signal streams and provisioning sources. Every existing catalogue action for those objects is a *change* (`*.manage`, R3), so a console that only reads would have to hold R3 change authority to see anything. No spec names a read action for these objects, and ZID-API-001 §11 has no endpoint that creates a mandate from a grant. The items below are implemented now as `proposed` (action catalogue `name_source: proposed`, `note: "CR-010"`) and move to canonical once the owners accept them.

## Catalogue actions

| Action | Floor | Owner | Why |
|---|---|---|---|
| `authority.grant.read` | R1 / E1 | authority | `GET /v1/authority-grants`: list grants without holding `authority.grant` (R3) |
| `authority.approval.read` | R1 / E1 | authority | `GET /v1/approval-requests`: list approval requests and their current state |
| `mandate.read` | R1 / E1 | mandate | `GET /v1/mandates`: list mandates and their terms |
| `mandate.create` | R3 / E3 | mandate | `POST /v1/mandates`: a grantee attenuates its grant into a mandate for an executor (MANDATE §9-10) |
| `credential.read` | R1 / E1 | credential | `GET /credentials`, `/credential-configurations`, `/credential-issuer-trust`, `/relying-party-registrations` |
| `signals.stream.read` | R1 / E1 | signals | `GET /ssf/streams` without `signals.stream.manage` (R3) |
| `provisioning.source.read` | R1 / E1 | provisioning | `GET /provisioning/sources` without `provisioning.source.manage` (R3) |

The principal directory (`GET /admin/principals`) reuses the canonical `identity.read`; federation trust anchors (`GET /federation/trust-anchors`) stay under `tenant.identity_provider.change`, like `GET /federation/integrations`; verification adapters (`GET /verification-adapters`) use the `verification:read` scope, like `GET /verification-profiles`.

Every read goes through the protected-action PDP (authority chain, policy release, security state), never a role flag. Responses carry identifiers, states, limits and times only: no secrets, secret references, token or key hashes, pinned key material or raw PII.

## Mandate creation rules

`POST /v1/mandates` creates a mandate in `PENDING_APPROVAL` (the initial `agent.mandate_status`; activation stays `POST /v1/mandates/{id}:activate`). A mandate never exceeds its source grant:

- the caller must be the grant's grantee (`GRANTOR_AUTHORITY_MISSING`), and the grant must resolve to a valid chain now (`MANDATE_SOURCE_INVALID`);
- actions, resource scopes (pattern containment), purpose, tools, jurisdictions, value, count and data limits and the validity window must sit inside the chain's effective authority (`GRANT_SCOPE_WIDENING`, with the widened dimensions in the detail);
- the represented principal comes from the grant, never the request body; the creator is the caller.

All codes above are existing canonical codes (`contracts/error-codes`); no new code is introduced.
