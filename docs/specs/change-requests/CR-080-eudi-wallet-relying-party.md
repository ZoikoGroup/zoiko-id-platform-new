# CR-080: EUDI Wallet relying-party obligations, attestation classes and interoperability conformance (ZID-RCG-003 RCG-G11)

Status: open (domain, configuration, two hooks in the OID4VP verifier, the attestation classifier and the conformance tests built and tested; Zoiko iD is not a registered wallet-relying party anywhere, so the hooks only report; the owners confirm the points below).

## Why
Document 02, EU/EEA row: a qualified service only where the exact listed provider and service hold qualified status for the relevant date, and
wallet or attribute exchanges carry the applicable relying-party obligations. RCG-G11 asks for these extensions with an interoperability conformance
acceptance. The OID4VP verifier already builds HAIP request objects (x509_hash client id, encrypted direct_post.jwt responses, DCQL, SD-JWT and mdoc)
and refuses a tenant's relying party that is not registered for a purpose. It did not know Zoiko's own position as an EUDI Wallet relying party.

## What changes
- **Domain** (`credential/domain/eudi.py`, pure):
  - `check_request`: a request to an EUDI Wallet is made only under Zoiko's ACTIVE, in-date registration in that Member State, for a registered intended
    use and attestation type, for registered attributes only (more is refused, never trimmed), signed under the registered access certificate (the
    `x509_hash:` client id must equal the certificate hash the registration names).
  - `classify`: PID, QEAA, PuB-EAA, EAA, NOT_TRUSTED. QEAA only with qualified status at the issuance date; a PID only from a listed PID provider;
    otherwise the lower class with its reason (`QEAA_STATUS_NOT_ESTABLISHED`, `PID_PROVIDER_NOT_LISTED`).
  - `conformance`: the signed request object against the interoperability profile: direct_post.jwt, x509 client id with the chain in the header,
    vp_token, DCQL with dc+sd-jwt or mso_mdoc only, a nonce of at least 128 bits, a life of at most ten minutes, an https response URI, an encryption key.
- **Configuration** `backend/config/regulatory/eudi-wallet-registrations.yaml`, loaded by `infrastructure/eudi_registrations.py`. Ships with NO
  registration (one example is a comment).
- **Hooks in the verifier** (`application/verifier.py` `create`, through `application/eudi_gate.py`), only for EUDI attestation types (`eu.europa.ec.eudi.`,
  `urn:eudi:`): the registration check before the request is built, the conformance check after it is signed. Every other request is untouched.
  Switch `ZID_EUDI_WALLET`: `shadow` (default, logs), `enforce` (refuses with the reason code), `off` (refused outside local, test and dev).
- **Classification from official registers**: `eudi_gate.classify_presented` reads the issuer's official-register attestations (CR-073, scheme
  `EU_TRUSTED_LIST`, service `QEAA` or `PID`) at the issuance date, so a later withdrawal never rewrites what an attestation was.
- **Wording**: six reason codes in English, Spanish and French.
- **Tests**: unit (registration, classes, each profile departure, parsing) and database tests driving the verifier's own `create`: its signed request
  object conforms; plaintext responses and a missing certificate do not; no registration, another Member State and an unregistered attribute are
  refused; a non-EUDI request is untouched; QEAA appears only after a trusted-list attestation for that issuance date.

## Not in this change
- The class is computed by `classify_presented`; showing it in the presentation result (`respond`) changes that result's shape and is a separate change
  agreed with the verification-and-trust team.
- Registration in a Member State register, and the access and registration certificates themselves: obtained by the owners; the registry here records them.
- Wallet-instance attestation and wallet trust-list checks on the response side (the wallet provider's listing).

## Owner decisions (proposed values, none ratified)
1. Intended uses are the purpose codes (`KYC_ONBOARDING`), normalised from the request's purpose.
2. Qualified status is attested under scheme `EU_TRUSTED_LIST` with service `QEAA` (and `PID` for PID providers).
3. Default `shadow` until the first registration exists; then `enforce`.
