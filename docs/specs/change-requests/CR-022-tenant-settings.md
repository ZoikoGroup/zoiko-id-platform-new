# CR-022: Versioned tenant settings, enforced session limits and cryptography overview

- **Target:** ZID-UX-001 §19, §20, §28 (optimistic concurrency), UX-06; ZID-DATA-001 §1.1 (configuration); action catalogue · **Raised by:** Phase H H-6 · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.configuration_snapshot` | configuration | Append-only history of each tenant's settings per area (AUTHENTICATION, NOTIFICATIONS, BRANDING). The current value is the highest revision. A write names the revision it was based on; a concurrent change collides on the primary key and the second writer gets a visible conflict. RLS on `tenant_id`; update and delete are blocked by trigger. Already declared in `table-ownership.yaml`. |

## API

`/v1/settings` (all areas), `PUT /v1/settings/{area}` with `expected_revision` (409 `CONFIGURATION_CONFLICT` when stale), `/v1/settings/{area}/history`, and read-only `/v1/settings/cryptography`. The public `GET /v1/workspaces/{name}` also returns the tenant's chosen presentation (display name override, accent, support link).

## Actions

`configuration.read` (R1/E1), `configuration.manage` (R2/E2). Each change bumps the tenant's configuration revision (security revision for AUTHENTICATION), appears in its change history and leaves an E2 audit record.

## Rules implemented (engineering reading of UX-001 §19-20; owners to confirm)

- **Authentication:** a tenant may only *shorten* the platform's session lifetime (default 12 h, minimum 1 h) and idle timeout (default 30 min, minimum 5 min). Enforced when sessions are created and on every authenticated request.
- **Notifications:** only non-mandatory alert kinds can be muted; CRITICAL and HIGH notices cannot (UX §20). Muted kinds are not raised.
- **Branding:** a display name cannot contain the platform's name or imply platform staff; accent is one of six fixed colours; the support link must be https.
- **Cryptography:** platform-managed keys only; BYOK and HYOK report "not enabled". No key material is returned, only purposes, allowed algorithms and published key ids.

## Not covered (needs a product decision)

Allowed-authenticator restrictions, recovery policy, notification channels (email, chat) and customer-managed keys.
