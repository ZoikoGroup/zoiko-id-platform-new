# DPIA addendum (draft): keeping an identity-document photograph for human review (the reviewed, no-chip route)

**Status: DRAFT for the Data Protection Officer and counsel. Not approved and not signed. Nothing here is legal advice. The retention periods are engineering PROPOSALS with their reasoning, ready to accept, change or reject. No lawful
basis, transfer mechanism or retention period is decided by this document.** It extends `dpia-addendum-v2-evidence-DRAFT.md`. Prepared from the code as built on 2026-11-18 (CR-124, CR-125).

## 1. What changed, and why it needs its own assessment

On the reviewed route (route `WEB_REVIEW`, an alternate a market's signed policy must name) a person photographs their identity document in a browser and two different people employed by the verifying company decide. For them to
decide, the platform now **keeps the photograph**. Until now it measured and dropped it. A photograph of an identity document holds the highest-risk data this platform handles: a name, a document number, a date of birth, a portrait,
sometimes an address, and the document's own security features.

## 2. What is held, where, and what is never held

| Data | Held in | Form | Never held |
|---|---|---|---|
| The document photograph | The evidence vault | A clean JPEG (re-encoded by the photo service: no location, device or time metadata), **encrypted under the applicant's own erasable key** | The file as it arrived; any data group of a chip |
| A reference to it | `verify.idv_case_media` | Vault reference, SHA-256, the measured image checks (resolution, sharpness, glare, exposure, re-compression, printed-line readability), the capture time, the date retention ends | The image |
| A reviewer's permission to open it | `verify.idv_media_grant` | The reviewer, their stated reason (10 to 500 characters), minutes of life, up to three reads | The image |
| The decision | `verify.idv_case` and `verify.idv_case_decision` | State, reason codes, who signed, the pinned rules | Printed data: on this route **no digest of the name, number or birth date is kept either** (they are kept only on a chip route, to compare with the chip, and cleared after) |
| The claim an approval leaves | `verify.idv_case_claim` | The claim type, the grade (`REVIEWED_NO_CHIP`), the market, when it stops counting | The document |

## 3. Who sees it, and under what control

- **Applicant:** supplied it; may withdraw by asking for erasure.
- **Reviewers:** people the verifying company's own administrators authorise (the existing protected raw-evidence read action `evidence.object.read`, R3 and E3). A reviewer is never the applicant. They must give a reason, receive a grant that lasts five minutes and
  reads at most three times, and every grant is a stored record. The image is returned with `Cache-Control: no-store`, is never in a list or a log, and a newer photograph or a recapture request retires the old one.
- **Zoiko:** does not see it in ordinary operation. It is encrypted under a per-subject key held by the evidence runtime.
- **Not shared** with any provider or other tenant; no transfer outside the cell's region is made by this feature.

## 4. Proposed lawful basis, notice and retention (**DPO and counsel** decide)

| Item | Proposal | Reasoning |
|---|---|---|
| Lawful basis | To be decided per market. Candidates: the verifying company's legal obligation (identity checks it must perform), or consent where the person chooses this route over the chip | The route is an alternative the person may choose; where it is required by law the basis differs. Not for engineering to pick |
| Notice | `idv-reviewed-route-notice-DRAFT.md`, shown before the photograph is taken | A person must know a photograph is kept, for how long, who may look and why |
| Retention of the photograph | **7 to 14 days after the case closes, never more than 30**, set per market in `media_retention_days` (1 to 90 accepted by the platform) | The photograph is needed for review and a short appeal window; the longer an identity document image is held, the larger the harm of a breach. The platform keeps nothing if a market sets no period |
| Retention of the claim | Per market in `result_valid_days` (1 to 730 accepted); none means no claim is issued | How long an approval should count is a risk decision for the market's owners |
| Retention of the case and decisions | As the DPO's records schedule says; they hold codes and ids, no document data | They are the audit record of a decision |
| Grants | Kept with the case | They show who looked and why |

## 5. Risks and the controls built

| Risk | Control in code | Residual risk |
|---|---|---|
| A reviewer looks without need | Reason required and stored; protected action; five minutes, three reads; not the applicant; only while the case is open | A reviewer can still misuse what they are shown. Training and review of grants are the owners' |
| The image leaks from storage | Encrypted per subject; ciphertext only in the database; erased by key destruction on a rights request | Depends on the key service. A development key store is not for production |
| The image carries hidden data (location, device) | Re-encoded by the photo service before anything is read or kept (proved in the real container, `tools/ocr-sidecar/smoke_in_container.py`) | The service must be running; without it the upload is refused |
| A malicious file | Scanned for malware first; a cell with no scanner refuses rather than pretends to scan; only JPEG and PNG, size and pixel caps | The simulator scanner in local and test finds only the test string; a deployed cell needs a real scanner |
| Retention overruns | The photograph carries its own end date; the retention sweep erases the bytes at that date | **Not yet proved end to end** that the sweep removes these objects in a deployed cell |
| An erased person is still treated as verified | A rights request revokes the claim and retires the photograph | The pseudonymous applicant id remains in the closed case record |
| A wrong decision about a person | Two different reviewers; defined reasons to reject; recapture is not a finding against them and costs no attempt | An applicant has no stated appeal route in this feature |
| Discrimination in review | No engine and no score decides; the reviewers decide, with the measured checks shown only as capture quality | Reviewer variation is not measured |

## 6. Not yet done (for the owners)

- Decide the lawful basis and the retention periods, and record them per market in `market-policies.yaml`.
- Approve the notice and give it a version.
- Train reviewers; define what a reviewer may reject for and how an applicant appeals.
- Confirm the retention sweep removes the photographs, with a test in a deployed cell.
- Read the licence of the photo service's components and sign the register (`photo-service-licence-sheet.md`).
- Sign this assessment.
