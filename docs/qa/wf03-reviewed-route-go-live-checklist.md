# Reviewed (browser-only) identity route: go-live checklist

Status 18 Nov 2026. Engineering is built (CR-124, CR-125). This lists what stands between it and a controlled pilot, who closes each item, and what engineering can still do. Nothing here is signed.

| # | Gate | Owner | Engineering state | Closed when |
|---|------|-------|-------------------|-------------|
| 1 | Market policy: `media_retention_days`, `result_valid_days`, `review_sla_hours` | Product owner + DPO | Config path built; repo ships no market (route refuses to run) | A reviewed `market-policies.yaml` entry is merged (the local GB file is test only) |
| 2 | DPIA, lawful basis, notice for keeping an identity-document image | DPO / counsel | Drafts in `docs/privacy/*-DRAFT.md` | Signed and dated |
| 3 | Photo-service licence reading (Tesseract, Pillow) | Compliance | Register entry present, unsigned | `model_licence_confirmed` + `licence_verified_on` recorded |
| 4 | Architecture ratification of the review-photo custody exception (VERIFY-22) | Architecture owner | Guard amended with a pinned exception | Ratified in a CR decision |
| 5 | Reviewer training and a named reviewer pool (at least 2 independent) | Operations | Reviewer screen and queue built | Training record per reviewer |
| 6 | Run `wf03-reviewed-route-uat.md` with a real photo, browser and phone | QA | Script written; never run | Signed result file |
| 7 | Photo service deployed in a cell; retention sweep proved on real photos | Platform / SRE | Image built, smoke-tested in a container (12/12) | Deployed, sweep observed |
| 8 | Decision: accept `REVIEWED_NO_CHIP` grade downstream? | Product + risk | Nothing downstream reads the claim yet, so nothing filters on it | Written decision; a filter is then a small change |
| 9 | Decision: run without authenticity checks and face match (reviewers by eye) | Risk / compliance | Authenticity evidence gate built, ships with no policy; no face engine approved | Written risk acceptance for the pilot scope, or the checks are built |
| 10 | G0 to G3 sign-offs | Governance | Packs exist | Signed |

## What engineering cannot do from here

Real photographs, reviewers and devices (6); legal sign-offs (2, 3, 10); owner decisions (1, 4, 8, 9); deployment (7); real authenticity specimens and a licensed face engine (9).

## Pilot boundary suggested

Internal staff or one small market, a reviewer team of at least 3, approvals marked as reviewed without chip, no downstream use of the grade until item 8 is decided.
