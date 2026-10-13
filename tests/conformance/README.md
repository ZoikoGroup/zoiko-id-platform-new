# Protocol conformance (Phase E exit evidence)

Zoiko iD's protocol surfaces are certified with the OpenID Foundation conformance suite where one exists. The rest are covered by in-repo protocol tests plus named interop runs.

| Plan key | Suite plan | What it certifies |
|---|---|---|
| `oidc-op-basic` | `oidcc-basic-certification-test-plan` | OpenID Connect Basic OP: code flow, `private_key_jwt` |
| `oidc-op-config` | `oidcc-config-certification-test-plan` | Discovery metadata |
| `fapi2-dpop` | `fapi2-security-profile-final-test-plan` | FAPI 2.0 Security Profile with DPoP |
| `fapi2-mtls` | `fapi2-security-profile-final-test-plan` | FAPI 2.0 with mTLS client auth and certificate-bound tokens |
| `oid4vp-verifier-haip` | `oid4vp-1final-verifier-test-plan` | OID4VP verifier, HAIP: `x509_hash`, `direct_post.jwt`, SD-JWT VC |
| `oid4vp-verifier-haip-mdoc` | same | the same for ISO mdoc |
| `ssf-transmitter` | `openid-ssf-transmitter-test-plan` | SSF transmitter (CAEP/RISC, push) |

Variants and configuration live in [`plans.yaml`](plans.yaml). Plan names are pinned to the suite release recorded there. `backend/app/tests/unit/test_conformance_plans.py` fails if a plan claims a variant the server does not advertise, for example DPoP, mTLS-bound tokens, `x509_hash` or `direct_post.jwt`. It also fails if an uncovered profile names a test that does not exist.

## Running

Run against a PERF or STAGING cell only. The runner refuses a cell whose name contains `prod`.

1. Configure the cell for the plan:
   - FAPI plans: register the suite's clients with profile `FAPI2`.
   - OID4VP plans: set `ZID_VERIFIER_CLIENT_ID_PREFIX=x509_hash` and `ZID_VERIFIER_CERTIFICATE_CHAIN` (a CA-issued certificate for the verifier key), and create presentations with `response_mode: direct_post.jwt`.
2. Export what the plan needs: `CONFORMANCE_SERVER`, `CONFORMANCE_TOKEN`, `ZID_CELL`, `ZID_ISSUER`, `ZID_API`, and the client variables named in `plans.yaml`. JWKS values may be JSON strings. Secrets come from the secret store, never from this repository.
3. Run:

```
python tests/conformance/run_conformance.py --list
python tests/conformance/run_conformance.py oidc-op-basic oidc-op-config fapi2-dpop fapi2-mtls
python tests/conformance/run_conformance.py oid4vp-verifier-haip oid4vp-verifier-haip-mdoc ssf-transmitter
```

The runner creates each plan and runs every module, waiting while a module needs browser interaction. It exits non-zero unless every module finishes as PASSED, REVIEW, SKIPPED or WARNING. Export each plan's results from the suite UI and attach them to the release: they are the evidence for the Phase E exit gate.

## Not covered by an OpenID Foundation suite

| Profile | In-repo tests | Interop before production enablement |
|---|---|---|
| SAML 2.0 SP | `test_federation_saml_pg.py`, `test_federation_saml_encrypted_pg.py` | Entra ID, Okta, ADFS |
| SCIM 2.0 service provider | `test_scim_pg.py` | Entra ID provisioning validator |
| WebAuthn RP | `test_webauthn_pg.py` | FIDO conformance tools (RP) |
| W3C VCDM 2.0 (VC-JOSE-COSE) | `test_vcdm_pg.py`, `test_vcdm.py` | W3C VC test suite |
