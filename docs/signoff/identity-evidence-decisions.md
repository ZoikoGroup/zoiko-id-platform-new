# Identity evidence without a vendor: what the documents settle, what the code enforces, and what the owners must decide

Prepared 2026-11-13 from Documents 01, 03 and 04 (v2.0, 10 Nov 2026) and the repository. This is a decision record for the owners. It approves nothing.

## What the documents say

| Source | What it says | Consequence |
|---|---|---|
| Document 01 section 01 | Zoiko iD "is not a narrow identity-verification reseller"; its job is to bind an actor, a claim, a valid source, a permission, a decision and a history. | A vendor face check is one source of identity, not the product. |
| Document 01 section 01.1 | Evidence is a ladder (SELF_DECLARED, DOCUMENT_INTEGRITY, ISSUER_AUTHENTICATED, PRIMARY_SOURCE_CONFIRMED, CURRENT_ELIGIBILITY, CONSENTED_PORTABLE_ASSERTION). Each level "proves only its own sentence". | An issuer's signature is ISSUER_AUTHENTICATED. It is never PRIMARY_SOURCE_CONFIRMED. |
| Document 03 section 06, PERSON_IDENTITY | Source: "Document/NFC/authority or qualified source. Name match alone is insufficient." | A chip, an authority-issued credential or a qualified source can establish a person. A name match cannot. |
| Document 04, workflow A and the evidence table | "Signed chip checks" and an in-person, manual or non-biometric route must exist. | A chip route and a human route are expected alongside any camera route. |
| Document 01 section 15 | "Issuer network partnerships: use authoritative registrars and regulators where permitted; commercial aggregators only with rights. Legal + Procurement: NOT VERIFIED." "No invented policy." | Which issuers count is not engineering's to decide. |
| Document 03 RCG-G11 | A service is accepted only when the exact listed provider and service held the status on the relevant date. | For EU PIDs the rule is already stated: an official register must list the provider on the issuance date. |

## Decision ID-1: which issuers and credential types count as proof of who a person is

**Settled by the documents and enforced by the code today**
- The credential type must be one the owners listed (`backend/config/identity-credentials/identity-credentials.yaml`). The file ships with one SAMPLE type and the two EU PID type names, and trusts nothing by itself.
- The issuer must also be pinned in the typed trust table for that exact type. No issuer is trusted for everything.
- An EU PID additionally needs an official-register attestation (CR-073) that lists its issuer as a PID provider for the stated Member State on the day the credential was issued. No Member State, no issue date or no listing means INDETERMINATE (`PID_PROVIDER_NOT_LISTED`).
- The holder must be bound (a JWT holder proof, or a `did:key` presentation for JSON-LD), the status must be confirmed active, and a copied credential never identifies anyone.
- The best answer is VERIFIED_LIMITED. It always states: an issuer's attestation, not a population-register check; key possession, not a live person.

**Not settled by the documents: needs approval**
- The list of non-PID issuers (a national eID scheme, a bank scheme, an eIDAS-notified scheme) and their credential types.
- Whether any commercial aggregator is acceptable. The documents say only with rights.

**Proposed baseline, from the documents' own wording:** authoritative registrars and regulators only; one row per issuer and credential type, each with the evidence below; nothing added without all of it.

| Evidence required before an issuer is pinned | Why |
|---|---|
| Contract or legal right to rely on its credentials | Document 01 section 15, "commercial aggregators only with rights" |
| The issuer's status on an official list or register, dated | Document 03 RCG-G11 |
| The issuer's public keys, pinned, with a rotation plan | Typed trust table |
| A status list the platform can read, and its revocation delay | An unreadable status is never acceptance |
| The jurisdictions and purposes it may be relied on for | A result is scoped to country, purpose and policy version |
| Legal and Privacy review of the data it discloses | Only name and birth date are returned |

Approvers: Legal and Procurement (Document 01 section 15), with Privacy and Security.

## Decision ID-2: feeding a credential-based identity into the engagement check's IDENTITY dimension

**Why it is not wired in, and should not be wired in silently**
- Today the IDENTITY dimension reads valid identity assertions from the verification module. Those come from identity-proofing sessions under an approved assurance profile.
- The platform's own tier rule is: IAL-Z2 needs a binding and liveness "or an accepted equivalent", and `ACCEPTED_EQUIVALENT_CREDENTIAL` is a recognised proof source. Whether a holder-bound wallet credential is an accepted equivalent is exactly the approval Document 01 section 15 reserves. Our check says `LIVE_PERSON_NOT_ASSESSED`, so by the platform's own rule it needs that approval before it can stand in for a proofing session.
- Document 03 says name matching alone is insufficient. Linking a credential's holder to a Zoiko principal by name would be exactly that. The link has to be that the principal presented the credential inside their own authenticated session.

**Proposal for the owners**
1. Approve, for named purposes only, an assurance profile "accepted equivalent: a holder-bound credential from an issuer approved under ID-1" at the level they choose.
2. Require the credential to be presented inside the principal's own authenticated session, so the principal is the presenter.
3. Record it as an assertion with its provenance (issuer, credential type, register listing relied on) and an expiry no later than the credential's own, never longer.
4. Engineering then builds the bridge (a session and an assertion from the credential check). Until then the engagement check keeps reading provider assertions only, and nothing here can raise a person's identity level.

Approvers: Product, Privacy, Security and Legal.

## The same rule applies to the passport chip route

A genuine chip (`POST /v2/documents/chip-verify`) shows the document is authentic. It does not show the holder is the person in front of the phone, and it cannot detect a clone. On its own it is DOCUMENT_INTEGRITY and ISSUER_AUTHENTICATED for the document, and never a person. It becomes identity only with a holder check (a face comparison or an in-person step) under an approved profile. No chip data other than the verified MRZ fields is returned, and the photo is never read.

## What engineering has built, and what it cannot decide

| Item | State |
|---|---|
| Identity from a wallet or eID credential | Built, fail-closed, trusts nothing until issuers are pinned (`POST /v2/identity/credential-verify`) |
| EU PID provider listing gate | Built, uses the official-register attestations |
| Passport chip passive authentication | Built, trusts no country until CSCA certificates are configured (`config/trust/csca/`) |
| Issuer list for non-PID credentials (ID-1) | Owners |
| Accepted-equivalent profile and the bridge into the IDENTITY dimension (ID-2) | Owners decide, then engineering builds |

Signatures: Legal and Procurement ______  Privacy ______  Security ______  Product ______  Date ______
