# CR-138: a company enters its identity provider's client secret in the console, and the platform seals it

Source: owner request, 20 Nov 2026 ("for a real user, how can they complete"). Builds on the enterprise federation (ZID-AUTH-001 section 9) and the write-only connector credential of directory sync. Status: built 20 Nov 2026.

## The problem

A federation connection that authenticates to the provider with a shared secret (`client_secret_basic`, the way a provider such as Auth0 expects) could only name a secret kept in the OPERATOR's secret store (OpenBao in production). A company administrator has no access to that store, so a real customer could not finish connecting such a provider; the only choice offered, a signed key, is not accepted by many providers.

## What was built

- `POST /federation/integrations` and `POST /federation/integrations/{id}:rotate` take a write-only `client_secret` (never returned, logged or echoed). It is refused with a signed key, and together with the name of a stored secret.
- The platform registers an OIDC identity-provider connector for the connection (`register_signin_connector`, source `federation_connection:<id>`), seals the secret as that connector's credential under the cell's data key with the tenant bound in (the same store directory sync uses), and keeps only the reference `connector:<connector id>` on the connection. Rotating with a new secret replaces it at once under the same reference.
- At sign-in the token exchange resolves `connector:<id>` to the sealed secret for that company's own connection; any other value is still read from the operator's secret store, so operators who keep their secrets there are unaffected.
- The connect and rotate forms have a "Client secret" password box; the stored-secret name remains as an optional alternative.

## Not changed

Signed-key authentication, the trust and approval rules, and who may connect a provider are unchanged. A connection's Client ID still cannot be changed after it is created.

Tests: `tests/component/test_federation_sealed_secret_pg.py`, `tests/unit/test_security_controls.py` (sealed secret is the one sent), the identity-provider screen tests.
