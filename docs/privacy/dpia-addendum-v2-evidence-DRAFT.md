# DPIA addendum (draft): /v2 evidence uploads, document validation, organization lookup and age checks

**Status: DRAFT for the Data Protection Officer. Not approved and not signed. Nothing here is legal advice. The retention periods below are
engineering PROPOSALS with their reasoning, ready for the DPO to accept, change or reject; no lawful basis or transfer mechanism is decided.**
It extends `dpia-credential-trust-DRAFT.md`. Prepared from the code as built on 2026-11-11 (ZID-INT-002 /v2, CR-071).

## 1. What is new, and what each thing holds

| Feature | Personal data held | Where | Never held |
|---|---|---|---|
| Evidence upload (`POST /v2/evidence/uploads`) | The document bytes (only once scanned CLEAN), its declared and sniffed type, size, SHA-256, the uploading principal, an optional subject reference, scan verdict, timestamps | `evidence.upload` in the cell database (the bytea column exists only when `state='READY'` and `scan_state='CLEAN'`) | Bytes of an infected, unscanned or failed upload; EXIF or other metadata of a JPEG or PNG (stripped before storage) |
| Document validation (`POST /v2/documents/validate`) | For a stored upload only: the result (statuses, reason codes, detail, digest) | `evidence.document_validation`, deleted with its upload | A pasted document (checked in memory and discarded); the signer's certificate beyond subject and issuer names in the result |
| Trust configuration (`config/trust`) | None about people. Public CA certificates, trusted lists and CRLs | Files in the deployment | n/a |
| Organization lookup (`POST /v2/organizations/verify`) | Nothing stored. Company facts from an official register pass through the response | none | The names of directors or controlling persons (never returned) |
| Age check (`POST /v2/age-checks`) | Nothing stored | none | The birth date, an age or an estimate (never returned or kept) |

Signature validation reads the signer's certificate from a PDF (a name, sometimes an identifier). It is reported in the response and kept only as
part of a stored validation result.

## 2. Proposed retention (**DPO** decides)

| Data | Proposed period | Reasoning | Status in code |
|---|---|---|---|
| Upload bytes | 30 days after READY, never more than 90 | Evidence is needed for the check it supports and a short appeal window; long retention of identity documents is the main risk of this feature | `ZID_UPLOAD_RETENTION_DAYS` is read as one number of days. Unset means the cell refuses uploads. Local compose sets 30 as an engineering default only |
| Upload row (metadata, no bytes) | Erased with the bytes, or when the subject is erased | Nothing is gained from keeping a record of a deleted document | Bytes erased by the retention job; metadata follows the privacy module's erasure |
| Stored validation result | Same lifetime as its upload | It describes that upload | Deleted with the upload (`ON DELETE CASCADE`) |
| Infected or failed uploads | Metadata only, 30 days | Needed to explain a rejection and to spot abuse; no bytes exist | Same job |
| Idempotency records | The platform default for idempotency | Replays only | Unchanged |
| Outbox events about uploads | Per the evidence module's event policy | Ids and states only | Unchanged |

Backups follow the privacy module's backup erasure window, which is also undecided (**DPO**).

## 3. Lawful basis, purpose and minimization (**DPO**, Legal)

- The purpose of an upload is the verification it supports for the relying party that sent it. The relying party is expected to be the
  controller for the document and Zoiko iD its processor for storage and scanning; this allocation is **not decided** and depends on the contract.
- Minimization built: only a declared type from an allow-list is accepted; size is capped; bytes are kept only when clean; image metadata is
  stripped; a validation of pasted content stores nothing; results are statuses and reason codes, not content.
- An age check deliberately cannot produce a "yes" from a typed date; the protection against a wrongly young or old finding is that
  uncertainty is UNDETERMINED with a documented check offered, never a refusal.

## 4. Security measures built

Signed 5-minute upload token (`zid-upload+jwt`); scan before keep and fail closed (a down scanner is SCAN_ERROR, never clean; a deployed cell with
no scanner refuses uploads); the real ClamAV daemon was exercised through the genuine protocol; content sniffing; row-level security by tenant;
the runtime role cannot delete (erasure is a SECURITY DEFINER function with an explicit tenant filter); trusted lists are authenticated by their
own XML signature against operator-pinned signers, and XML with a DTD, entity or external reference is refused before parsing.

## 5. Risks

| Risk | Mitigation | Residual / decision |
|---|---|---|
| Identity documents held longer than needed | Short proposed retention; bytes only when clean; subject erasure | **DPO**: the number of days |
| Malware in an upload | Scan before keep; fail closed | A signature database that is out of date misses new malware: the deployment must run `freshclam` (the local container could not refresh in this environment) |
| A valid signature mistaken for lawful authority | Every result states its limits; plain VERIFIED needs every proof | Policy decision on what a relying party may rely on |
| Trusting the wrong list signer | The tool refuses to run without an operator-confirmed signer | Someone must check the signer against the Official Journal |
| A company lookup used to profile individuals | No person is named; no authority implied | Legal: acceptable use terms |
| Age estimates used on children | Margin plus a buffer; near the line is UNDETERMINED | **DPO**: whether a caller's estimate from facial analysis is acceptable at all (it may be special-category biometric processing in the caller's hands) |
| Machine-drafted translations misleading a person | Reported as MACHINE_DRAFT_UNREVIEWED | A native-speaker and legal review before use in a real appeal |

## 6. Accessibility (replaces the earlier "no WCAG audit" line)

Automated browser audit (Chromium, axe-core, WCAG 2.2 A and AA tags, including colour contrast) of the eight sign-in-free pages at desktop width
and 320 px: 16 of 16 pass after two real defects were fixed (a second main landmark on the not-found page inside the sign-in layout, and a
horizontally scrolling pricing table that a keyboard could not reach). This is automated evidence. It does **not** replace a screen-reader run on
real devices, a manual keyboard review of the signed-in screens, or testing with disabled users. Those stay with QA.

## 7. Sign-off

Not signed. Required: DPO, Security and Privacy Officer, Global Regulatory Counsel, Zoikorum Product Owner. A signature block is deliberately not
provided here: it must be added by the people who sign.
