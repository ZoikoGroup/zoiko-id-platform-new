# CR-033: Tenant data deletion, rectification at the source, shared-identity disclosure, self-service OAuth clients

- **Target:** ZID-TENANCY-001 §10, §23 (TEN-24, TEN-25) · ZID-PRIV-001 rectification (PRIV-16) · ZID-AUTH-001 §6-7 (client registration) · **Raised by:** Acceptance linking, console wiring · **Status:** Open (built)

## Gap

- **TEN-25.** Closing a tenant (offboarding) stopped it signing in but deleted nothing; the spec asks that deleting one tenant cannot touch another, that held data is not destroyed, and that the owed dates (backup age-out, key destruction) are recorded.
- **PRIV-16.** A rectification request about a fact an external authority supplied (the company register) went to a person to edit by hand, which would make the stored fact disagree with its source.
- **TEN-24.** A reusable identity relied on from another cell had no defined disclosure: nothing stopped the source profile travelling with it.
- **Console.** The application page asked for an OAuth client ID that the console could not create.

## Change

| Table | Scope | Note |
|---|---|---|
| `platform.tenant_deletion` (onboarding) | tenant-scoped (RLS) | One row per closed tenant being deleted: who asked, status (`REQUESTED`, `HELD`, `COMPLETE`), the report, `backup_erasure_due`, `key_destruction_due`. |
| `org.registry_refresh_request` (organization) | tenant-scoped (RLS) | A request to re-read a register fact. The answer is appended to `registry_observation` as the superseding record; nothing is edited. |

| Operator capability | Risk | Note |
|---|---|---|
| `tenant.data.delete` | R3 / E3, two-person, 120 min, no break-glass | Ask for a closed tenant's data to be deleted. Destroys data and reads none. Conflicts with `evidence.admin` (SoD rule `delete-vs-evidence`). |

No new tenant action: OAuth client routes use the existing `integration.application.manage`.

API (operator): `POST` and `GET /v1/commercial/tenants/{id}/data-deletion`.
API (company): `GET`, `POST /v1/integration/oauth-clients`, `POST /v1/integration/oauth-clients/{id}:retire`.

## Rules

- **Deletion** requires a CLOSED tenant. The worker crypto-erases every vault subject through the same legal-hold evaluation as a single erasure; a held subject is kept and reported, the deletion stays `HELD`, and the company's dedicated key is **not** scheduled for destruction until nothing is held. Connector secrets are destroyed (fingerprints stay), cached security state and routing are dropped. Receipts, hashes and checkpoints stay valid. A repeated pass is harmless.
- **Rectification.** A fact whose kind is a registered external source is never overwritten: a replacement value is refused (`PRIVACY_SOURCE_AUTHORITATIVE`), the source is asked again at the next refresh pass, and what the person said is kept as a statement of disagreement. A self-asserted fact still goes to privacy operations.
- **Shared identity.** An assertion leaves its home cell only through the transfer gateway (class `DC2`) and only as: assertion id, claim type, level, times, a commitment, home cell. Fields are built from an allowlist (never copied from the row); provider, evidence, reason codes, the claim value and profile attributes are never sent. An approved transfer rule can narrow the fields further, never widen them.
- **OAuth clients.** No secret exists. A browser or mobile client is PUBLIC (PKCE); a server client registers only the public half of its key pair, and a pasted private key is refused. Redirects are exact https URLs (plain http only for `127.0.0.1` or `[::1]`). Self-service clients may ask only for `openid`, `verification:read`, `verification:execute`, `presentation:request`. The console's own first-party client cannot be retired here. Registration and retirement are audited as `integration.application.changed`.
- Modules stay acyclic: the client registry, the transfer gate, the rectification sources and the operator's deletion calls are joined in `main.py`.

Tests: `test_tenant_deletion_pg`, `test_privacy_pg` (rectification), `test_shared_identity_pg`, `test_oauth_client_registration_pg`, and the web unit test for the client form input.
