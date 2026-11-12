# CR-073 (design proposal): the remaining ZID-GAP-001 v2.0 items

Status: **design for approval. Built 2026-11-12: A1 CAdES, A3 multi-signature XML, A4 first half (issuer binding), the D product-line gate, E1 locale review record, B first part (MRZ). Part 2 at the end designs what is still open.** Written 2026-11-12 from the code as it stands after the XAdES, OCSP and EDC work. Each section ends
with what it needs from people, because most of what is left is gated by a provider, a legal basis or an owner decision, not by engineering effort.

Order of sections is the order I would build them in: A and B need only code; C needs your approval of a shape; D to F need outside inputs.

---

## A. Signature validation, finishing DTV-02 and DTV-04

Today: PDF (PAdES) and one-signature XML (XAdES) validate on `cryptography` and `signxml`; OCSP and CRLs are operator-supplied; timestamps are
RFC 3161 and checked by the repo's own verifier.

### A1. Standalone CAdES (detached `.p7s` over any file)

- New `evidence/application/cades.py`: `inspect_cades(signature: bytes, content: bytes, ...) -> list[Finding]`. It reuses `cms._parse_cms`,
  `build_chain`, `check_revocation` and `trusted_time_from_token`; the only new part is the signed content: the digest is of the caller's `content`
  (detached) or of the encapsulated content (attached), instead of a PDF byte range.
- Same refusals as PDF: SHA-1 and unknown algorithms are UNSUPPORTED, a missing `signed attributes` block is refused, the signer certificate must be
  the one the CAdES `signingCertificateV2` attribute names.
- API: `POST /v2/documents/validate` gains an optional `signature_base64` field. With it, `declared_type` may be any allowed type and the `signature`
  and `seal` checks run over the pair. No new route, no new stored data.
- Tests: the existing `pdf_signing` fixtures already produce real CMS; add a detached-signature builder and break one property per case, as for PDF.
- Not covered: CAdES-A archives (see A2).

### A2. Long-term validation (DTV-04)

What "long-term" means here, kept small and honest:
- Accept and *verify* what already exists inside a file: embedded revocation values (`RevocationInfoArchival`, XAdES `RevocationValues`), embedded
  certificate values, and document timestamps (`ETSI.RFC3161` in PDF, `SignatureTimeStamp` / `ArchiveTimeStamp` in XAdES).
- Verify an archive timestamp chain: each `ArchiveTimeStamp` covers the signature plus all evidence before it; the chain is valid when every stamp
  verifies against a configured TSA root *at its own time* and each later stamp is made while the earlier TSA certificate was still valid.
- Report an honest `ltv_state`: `NONE`, `BASELINE_LT` (embedded evidence), `ARCHIVE_VALID` (stamp chain intact), `ARCHIVE_BROKEN` (named stamp).
- **We do not preserve documents.** Producing new archive stamps for a customer is a preservation service (a regulated product line, DTV-04 "P1").
  This design only validates archives other parties made. If you want to *offer* preservation it is a separate CR with retention and a TSA contract.
- Needs: TSA roots from the operator (config already supports them). No new database tables.

### A3. More than one signature per XML document

- Today `xades.py` refuses to guess when a document has two `ds:Signature` elements, because `signxml` verifies one signature per call.
- Design: verify each signature on a deep copy of the document in which every *other* signature is replaced by an empty placeholder **only when the
  document's own enveloped-signature transform would already exclude it**; otherwise keep it, because it is signed content. Findings are indexed
  per signature, and `signature_result` already aggregates by the weakest finding.
- Risk: signature-wrapping attacks are the classic XML failure. Mitigation: every signature must reference an element by an ID that is unique in
  the document (duplicates refused), and the signed element must be the one the caller's result reports. This needs its own adversarial test set
  (wrapping, duplicate IDs, moved `SignedProperties`) before it is switched on; until then the "unsupported" answer stays.

### A4. JSON-LD EDC and issuer binding (GID-051)

- JSON-LD EDC: a Verifiable Credential with an embedded or JWS proof. `vc_verify.py` already verifies vc+jwt; add the `Kind.EDC` branch that, instead
  of refusing, reads the claim fields through the same name-based mapper and verifies the proof with the issuer keys from the trust table. A
  Data-Integrity proof (`eddsa-rdfc-2022`) needs RDF canonicalization, which is not in the repo; it stays refused with a named reason.
- Issuer binding: today `issuer_binding` is `NOT_ASSESSED`. Design: compare the seal certificate's organization (`O`, `organizationIdentifier`) with the
  `awarding_body` through the **issuer trust graph** (GID-038) rather than by string: the awarding body must resolve to an issuer record, and that
  record must list the sealing certificate's fingerprint or organization identifier. Result values: `BOUND`, `NOT_BOUND`, `ISSUER_UNKNOWN`.
  Never a fuzzy name match.
- Rescinded degrees (GLB-36): an EDC has no live status. The answer stays "valid seal, status unknown" until a primary-source check runs;
  the route should offer the existing academic claim check as the follow-up, not pretend the seal settles it.

---

## B. Document intelligence (DTV-16), scoped by what is provable

Needs the template list from you (see "Decisions"), but the platform part can be designed now.

- **Principle:** extraction never proves anything. Output is `extracted_fields` with a per-field source (`MRZ`, `BARCODE`, `PDF_FORM`, `OCR`) and a
  confidence; OCR-only fields can never reach `ISSUER_AUTHENTICATED` (invariant already in the document, section 11, and GLB-tests).
- **Deterministic first, no model:** (1) MRZ parsing with check digits (ICAO 9303) for passports and ID cards; (2) 2D barcode / QR payloads that
  carry a signature (the issuer's own seal verified through A1); (3) PDF form fields and embedded XML (diplomas that are sealed PDFs).
  These are testable, free of any biometric or model-vendor question, and cover the highest-value cases.
- **OCR and layout templates:** an optional adapter behind the existing `ProviderAdapter` guards. Open-source OCR (Tesseract) runs in the ClamAV-style
  sidecar container; a template is data (`config/document-templates/<issuer>/<version>.yaml`: field regions, expected patterns, issuer id,
  valid-from) so adding an issuer is a config change with a fixture, not code.
- **Tamper signals:** reported as signals, never verdicts: PDF incremental updates, producer/creator mismatch with the issuer template, font and
  metadata anomalies, image error-level differences. Each is `SIGNAL` with an explanation; none can turn a result into NOT_VERIFIED by itself
  (human review route, DTV-09, decides), which keeps the "no secret fraud-score-only denial" rule.
- **API:** `POST /v2/documents/extract` (stateless, same upload rules, same scanner gate). Result feeds `claim checks` as `evidence_ref` only as
  SELF_DECLARED evidence.
- **Not designed here:** face matching, liveness, selfie comparison. Those are DTV-06 and need a certified PAD engine (see Document 04 matrix).

---

## C. GID-064 pricing and metering

Already designed in `CR-070-credential-check-meter-PROPOSAL.md` (two meters: `credential.check`, `credential.sensitive_check`; unpriced, recording off).
This section only adds the **activation gate**, which GLB-35 requires:

- A credential check line can be quoted or billed only when a **signed price book** entry exists for (meter, region, currency, effective date). The
  quote rules already reject a missing price (`PRICE_MISSING`); add the region and currency to the lookup so a UK GBP price can never price a US USD
  checkout, and add a test with exactly the GLB-35 fixture.
- Provider pass-through (what a source or aggregator charges us) is a separate cost line already supported by vendor-cost tracking (CR-029); it is
  shown, never hidden inside the per-check price.
- Needs from you: (1) approval to change the ratified meter register, (2) CFO and CCO price numbers per region, (3) the rule for when a check
  counts as completed (CR-070 proposes: reached a source or an outreach concluded; not for refusals or source outages).

---

## D. Financial adjacent products, DTV-26 to 28 and 32 to 34

All six are regulated or need a licensed partner. The design is the same for each: **a guarded product line that exists as an interface, is OFF, and
cannot be switched on without a recorded legal basis, a partner contract and an owner approval.** Nothing here collects data.

Common shape (mirrors `provider_adapter.py` and the certification registry gate):
- `ProductLine` record: `code`, `jurisdictions`, `legal_basis_ref`, `partner_contract_ref`, `state` (`DESIGN | BLOCKED | STAGING | LIVE`), `approved_by`.
  Shipped config lists every line as `DESIGN`; a test fails if any is `LIVE` without both references (the same pattern as country capabilities).
- A route for a line that is not `LIVE` answers 422 `service_unavailable` with the line's state, never an empty success.
- Events and audit fields are defined now so a partner adapter later plugs in without a contract change.

| Item | What a partner provides | What we would own | Hard gates |
|---|---|---|---|
| DTV-26 open banking | An authorised AISP/PISP or aggregator (account-ownership assertion) | Consent record, purpose binding, the assertion as a claim (never account data) | Partner licence, bank-data consent, PSD2/regional rules |
| DTV-27 payment ownership | The existing Stripe integration already tokenises; add a "method belongs to the verified person" assertion from the provider's own checks | PCI-safe boundary: we never see a PAN, only provider tokens and the provider's result | PCI scope statement from Finance/Security |
| DTV-28 source of funds / wealth | Nobody: it is questionnaires plus evidence plus review | Versioned questionnaire definitions (data, like country packs), evidence via the existing upload path, reviewer queue (existing review machinery), signed outcome | Compliance officer defines the questionnaires; special-category handling in the DPIA |
| DTV-32 transaction monitoring | A rules engine or vendor fed customer transactions | Only the integration shell (rules as versioned data, alert to case) | Separate regulated product line; Legal approval; we do not hold transaction data today |
| DTV-33 crypto Travel Rule | A VASP protocol network (e.g. a TRP/IVMS101 provider) | IVMS101 payload validation, counterparty VASP registry checks, message record | Partner, jurisdiction thresholds, Legal |
| DTV-34 on-chain screening | A blockchain analytics provider | Risk-result claim with provenance; no verdict without review | Partner, risk model disclosure, Legal |

Build order if approved: the shared `ProductLine` gate and tests first (one slice, no partner needed), then DTV-28 (the only one that is mostly ours),
then DTV-27 (uses an existing provider). DTV-26, 32, 33, 34 stay interfaces until a partner exists.

---

## E. GID-062 accessibility and localization

What engineering can do, in order:
1. **Reason-code text in more locales:** the catalogue and fallback exist (`reasons.py`, Spanish and French drafts). Design the review workflow: each
   locale file carries `status: DRAFT | REVIEWED` and the reviewer's name and date; the API reports the locale it *really* used and whether that text
   is reviewed (`reason_text_review: "DRAFT"`), so a machine draft is never presented as approved wording. A test fails if a locale marked
   REVIEWED has no reviewer.
2. **Right-to-left and non-Latin rendering:** add a fixture set (Arabic, Hebrew, Devanagari, CJK names) to the web verifier page and the claim
   screens; assert `dir` handling, bidi isolation of names (`<bdi>`), and that no layout depends on Latin text width. Playwright with axe (the
   public-page audit exists) extends to these pages.
3. **Accessible alternative to selfie capture:** the selfie step lives in the identity providers' screens, so this is a **requirement on the provider
   contract and a routing rule on our side**, not a screen of ours: the session offers `alternative_routes` (document check in person, trusted
   assisted route, manual review) from configuration (Document 04, PR-03 already exposes alternatives); a person who cannot use the camera is moved to
   one of them without a failed attempt being recorded against them. Test: choosing an alternative never produces a REJECTED outcome by itself.
4. **WCAG 2.2 AA evidence:** automated axe runs are not a conformance claim. The deliverable is a conformance report template (criterion, page,
   method, result, tester) filled by a human auditor; engineering supplies keyboard-only and screen-reader test scripts.

Needs: translators (per language), a WCAG auditor, and the identity provider's accessible-route commitment in the contract.

---

## F. Decisions and inputs needed

| # | Decision or input | From | Unblocks |
|---|---|---|---|
| 1 | Approve building A1, A2 (validation only), A3 (with its adversarial tests), A4 | You | Section A |
| 2 | Is preservation (creating archive stamps for customers) in scope? Default proposed: **no** | Product, Legal | A2 scope |
| 3 | Which document types and issuers should extraction cover first (passport MRZ, which diplomas, which licences)? Default proposed: MRZ plus sealed PDFs only | Product | Section B |
| 4 | Approve changing the meter register (CR-070) and supply region prices | You, CFO, CCO | Section C |
| 5 | Which of DTV-26 to 28 and 32 to 34 are real roadmap items, and which partners exist or are being sought | CTO, Legal, Product | Section D beyond the shared gate |
| 6 | Translators and a WCAG auditor; identity-provider accessible-route clause | Product, Procurement | Section E |
| 7 | TSA roots, trust anchors, trusted lists and CRL/OCSP sources for `config/trust` | Operator | Everything in A in practice |

## Proposed first slice if approved

A1 (CAdES), then the shared `ProductLine` gate from D, then the locale review status from E. Each is code and tests only, with no outside input.

---

# Part 2 (2026-11-12): detailed design of what is still open

Written after the first slice and the second build pass. Built since Part 1: standalone CAdES (A1), more than one signature per XML document with a strict
engine and wrapping-attack tests (A3), the EDC issuer binding through the issuer trust graph (A4, first half), the product line gate (D, shared part), the
locale review record (E1), and MRZ extraction (B, first part). Still open, in the order I would do them:

## P2-1. Long-term validation (A2) in detail

**Where the evidence lives in a file**
- PDF: a `/DSS` dictionary in the catalog holds `/Certs`, `/OCSPs`, `/CRLs` and per-signature `/VRI` entries (keyed by the SHA-1 of the signature value); a
  later `DocTimeStamp` (`/SubFilter /ETSI.RFC3161`) is an incremental update that covers everything before it.
- XAdES: `CertificateValues` and `RevocationValues` (unsigned properties), then `SignatureTimeStamp`, then one or more `ArchiveTimeStamp`.

**Steps, in order, each reported on its own**
1. Extract the embedded certificates, CRLs and OCSP responses and add them to the pools the validator already uses, labelled `EMBEDDED`. They count exactly
   like operator-supplied evidence: a CRL must verify against its issuer, an OCSP response must be signed by the issuer or a delegated responder. Embedded
   evidence never adds a trust anchor; anchors stay operator configuration.
2. Judge the signature at the trusted signing time, as today (signature time-stamp), but using the embedded revocation values that were fresh at that time
   rather than only values valid today. This is what lets an old signature on an expired certificate still validate.
3. Walk the archive chain oldest to newest. Stamp n must (a) verify against a configured TSA root, (b) have an imprint that is the hash of the signature plus
   all evidence and earlier stamps, (c) have been issued while stamp n-1's TSA certificate was still valid. A broken link names which stamp broke.
4. Report `ltv_state`: `NONE`, `BASELINE_LT` (embedded evidence only), `ARCHIVE_VALID`, `ARCHIVE_BROKEN`, plus `earliest_trusted_time`.

**Not in scope:** creating archive stamps (a preservation service, see section A2 above), and evidence-record syntax (ERS, RFC 4998) for non-XML files.

**Tests:** real fixtures built with the repo's TSA authority helper: a chain of two stamps, a stamp over the wrong data, a stamp issued after its predecessor's
TSA certificate expired, an embedded CRL that does not verify, an embedded OCSP response signed by a stranger, and an expired signer certificate that
validates only because of an earlier trusted stamp.

**Input needed:** TSA roots and the operator's trust anchors (already configuration).

## P2-2. JSON-LD EDC (A4, second half)

- Add the `Kind.EDC` branch in `vc_verify.py`: today it refuses with `EDC_SEAL_VALIDATION_NOT_AVAILABLE`. Replace the refusal with: read the claim through the
  same mapper, verify the proof with the issuer keys from the trust table, then bind by `issuer` id. A JSON-LD credential names its issuer by identifier, so
  the binding is an exact registry match, stronger than the name match XML needs.
- A `DataIntegrityProof` (`eddsa-rdfc-2022`, `ecdsa-rdfc-2019`) needs RDF Dataset Canonicalization. There is no such library in the repo and writing it by
  hand is how verifiers get cut. Options: (a) accept only JWS-secured and `vc+jwt` EDC (what exists), (b) add a vetted canonicalization dependency after a
  security review. Recommendation: (a) now, refuse (b)'s proof types with `DATA_INTEGRITY_PROOF_NOT_SUPPORTED`, and revisit when an EDC issuer you care about
  uses them.
- Revocation: EDC carries `credentialStatus`; reuse the Bitstring Status List reader the VC path already has.

**Input needed:** the decision between (a) and (b), and a sample EDC from a real issuer to test the field mapping (the mapping today is by element name).

## P2-3. GID-064 pricing and metering: implementation plan once approved

Nothing is built; the register is pinned by a test on purpose. The plan, so approval is a yes or a change request:
1. **Register:** add `credential.check` and `credential.sensitive_check` (unpriced, `bill_on_commit: false`, as in CR-070). Update the pinned test's expected
   set in the same commit, with the reason in the commit message.
2. **Closed sets:** the billable meters are a closed set in three database constraints and in code (`billable.METERS`, `METER_SKU` in `rating.py`, the SKU
   rows). One migration per constraint (new `commercial_00NN` files), then the code constants, then SKU rows. This is one atomic slice: a half-applied set
   breaks quoting.
3. **Recording point:** call `record_billable` from `perform_check` (and from the outreach conclusion) in the same transaction as the check, key derived from
   the check id, so a retry cannot bill twice (the `credential.issue` pattern). Refusals before a source is asked, and source outages, record nothing.
4. **Price book gate (GLB-35):** add region and currency to the price lookup key and fail with `PRICE_MISSING` when there is no signed entry for (meter,
   region, currency, effective date). A test with exactly the GLB-35 fixture: a GBP price for the UK must never price a US USD checkout.
5. **Pass-through:** provider cost is a separate vendor-cost line (CR-029), never folded into the customer price.

**Input needed (not engineering):** approval to change the ratified register; CFO and CCO prices per region; the counting rule (CR-070 proposes: a check
counts once it reached a source or an outreach concluded).

## P2-4. Document intelligence (B), the parts after MRZ

Built: MRZ with check digits. Next, deterministic first, each with fixtures and none needing a model:
1. **PDF form fields and embedded XML** (sealed diplomas, e-invoices): read AcroForm fields and embedded XML attachments; a field read from a signed PDF whose
   signature validates is `SIGNED_FIELD`, otherwise `UNSIGNED_FIELD`. Source tags on every field.
2. **Signed 2D barcodes and QR codes:** parse the payload formats that carry an issuer signature (for example CBOR/COSE health certificates and ICAO VDS-NC);
   verify the signature with the issuer's key from the trust table; unsigned QR payloads are `UNSIGNED_PAYLOAD`, never evidence of authenticity.
3. **Templates (data):** `config/document-templates/<issuer>/<version>.yaml` with field regions, expected patterns, issuer id and a valid-from date. A template
   is an assertion about layout only; matching it proves nothing. It exists so OCR is bounded to known regions.
4. **OCR sidecar and tamper signals:** an optional container (Tesseract) behind the provider adapter guards; signals (incremental update, producer mismatch,
   font anomaly) are reported as `SIGNAL` with an explanation and can never turn a result into NOT_VERIFIED by themselves.

**Input needed:** which issuers' documents matter first (this chooses the template work), and whether OCR may run in your environment at all.

## P2-5. GID-062 accessibility and localization, the parts after the review record

1. **Right-to-left and non-Latin fixtures:** add Arabic, Hebrew, Devanagari, Thai and CJK names and reason text to the public verifier page and the claim
   screens; assert `dir` on the container, `<bdi>` isolation around names, no truncation of combining marks, and a layout that does not depend on Latin
   widths. Extend the existing Playwright and axe public page audit with these pages. This needs no outside input.
2. **Accessible alternative routing:** a `capture_alternatives` list in configuration per country and pack (document check in person, assisted route, manual
   review); an identity session offers it before and after any capture failure; choosing it, or failing capture for an accessibility reason, must never
   produce a rejection or a recorded failed attempt (test: GLB-30). The provider contract clause is a procurement item.
3. **WCAG 2.2 AA evidence:** a report template (criterion, page, technique, tester, tool, date, result, remediation, retest) and a keyboard-only and
   screen-reader script per page. Automated axe runs are an input to it, not a conformance claim. The auditor and the filled report are people.

**Input needed:** an auditor, translators, and the provider's accessible-route commitment.

## P2-6. Regulated product lines (D), the first real one: DTV-28 source of funds and wealth

The only line that is mostly ours. Design:
- **Questionnaire as data:** `config/questionnaires/<code>/<version>.yaml` (questions, answer types, evidence required per answer, jurisdiction applicability,
  risk weights left to the compliance officer). Versioned and immutable once a case uses it.
- **Case flow reuses existing machinery:** answers and evidence through the evidence upload path (scanned, retained under the privacy officer's value), a
  review queue with maker and checker (the credential review items), a signed outcome with reason codes and an appeal path.
- **Data protection:** source of funds is sensitive financial information. It needs its own entry in the DPIA and a retention value from the privacy officer
  before the line can move from DESIGN to STAGING; the line gate already refuses STAGING without a recorded legal basis.
- **Never a score-only decision:** the outcome is `CLEARED`, `NEEDS_REVIEW` or `NOT_CLEARED_PENDING_APPEAL`, always with a human reviewer on adverse results.

**Input needed:** the questionnaires and their legal basis from the compliance officer, and the DPIA entry. The five partner lines (DTV-26, 27, 32, 33, 34)
stay interfaces until a partner contract exists.

## P2-7. Decisions needed, updated

| # | Decision | From | Unblocks |
|---|---|---|---|
| 1 | JSON-LD EDC: accept only JWS-secured credentials now (recommended) or add RDF canonicalization after a security review | You | P2-2 |
| 2 | Approve the meter register change, the counting rule, and the region prices | You, CFO, CCO | P2-3 |
| 3 | Which issuers' documents first, and may OCR run here | Product, Security | P2-4 |
| 4 | Auditor, translators, provider accessible-route clause | Product, Procurement | P2-5 |
| 5 | Source of funds questionnaires, their legal basis, DPIA entry | Compliance officer, DPO | P2-6 |
| 6 | Partners for open banking, payment ownership, monitoring, Travel Rule, on-chain screening | CTO, Legal | the five partner lines |
| 7 | TSA roots, trust anchors, trusted lists, CRL and OCSP sources, and the EDC issuers' seal certificates for the issuer registry | Operator | P2-1 and every EDC binding |

## Suggested order, needing no outside input

P2-1 (long-term validation), then P2-5 item 1 (right-to-left fixtures), then P2-4 items 1 and 2 (PDF fields, signed barcodes).
