# CR-038: Adding an identity provider needs four values, not twenty

- **Target:** ZID-AUTH-001 §9, §19 (enterprise federation), ZID-UX-001 · **Raised by:** Console wiring (identity providers page) · **Status:** Open (built)

## Gap

Connecting an OpenID Connect or SAML provider asked for about twenty values, most of them copied by hand from the provider (two endpoints, a key set, a certificate). A person who had never done it could not fill it in.

## Change

`POST /federation/integrations:discover` (`tenant.identity_provider.change`, R3 like the rest of the page) takes an OpenID issuer, or a SAML metadata address, and returns what the provider publishes about itself: for OIDC the issuer, authorization and token endpoints and the signing keys (read from `<issuer>/.well-known/openid-configuration` and its `jwks_uri`); for SAML the entity id, the single sign-on address and the signing certificates. The result is returned, not stored: it is pinned when the integration is created, exactly as if it had been typed in, so nothing is fetched at sign-in.

Every request passes through `egress`: https only, a DNS host, a standard port, every resolved address public, no redirects, a six-second timeout and a size limit. An OpenID document must name the very issuer that was asked about (OIDC Discovery §4.3). SAML metadata is parsed with `defusedxml`, which refuses entity tricks. Failures return stable `DISCOVERY_*` codes with a plain next step.

The page: choose the provider (Microsoft Entra ID, Okta, Google Workspace, another OpenID provider, or SAML); fill in the name, the provider's address and, for OpenID, the client id; press "Find the rest for me". The endpoints, keys and certificates then sit under "Details" and everything expert sits under "Advanced settings" with safe defaults. If discovery fails the page opens "Details" so the person can fill them in by hand, as before.

Tests: unit tests with the provider mocked (endpoints and keys found, issuer mismatch refused, unfindable and incomplete providers, addresses that reach inside a network refused before any request, SAML metadata found and refused, XML entity tricks refused).
