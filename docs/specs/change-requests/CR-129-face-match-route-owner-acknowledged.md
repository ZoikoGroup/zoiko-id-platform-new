# CR-129: an owner-acknowledged face-match route (a live face against an uploaded document photograph)

Source: owner decision, 19 Nov 2026: complete identity verification with the platform's own face matching and uploads only, with no third-party service. Builds on CR-126 (the automatic route), CR-128 (the face lab) and CR-102 (the engine gate). Status: engineering built 19 Nov 2026. Not run with real photographs of other people.

## What the owner asked for, and what it cannot be

A person uploads a passport or an Aadhaar card, takes a live photograph, and the platform approves when the faces match above a threshold. That is built, as a separate route, `WEB_FACE`, that a market's policy lists on purpose.

It is **not** the engine-gated biometric flow and it does not pretend to be. Read this before relying on it:

- **The reference is the document's own printed photograph, which nobody authenticated.** Whoever uploads the document chooses the face on it. Anyone can put any face on an invented document, type nothing, take a live photograph of that same face, and match. The route therefore proves that the live face looks like the photograph that was uploaded, and nothing about who the person is or whether the document is real. The decision engine's own rule for this case is "reference not authentic".
- **No liveness test.** A photograph of a photograph matches too. No open-source liveness model is installed and none is evaluated.
- **No evaluated engine.** The engine (OpenCV YuNet and SFace) has no independent accuracy or attack evaluation, no impact assessment and no licence confirmation (`docs/signoff/face-engine-owner-pack.md`). The threshold is the owner's choice and nothing measured.

So an approval on this route leaves the **lowest grade**, `FACE_MATCH_NO_LIVENESS`, and its only assertion is `FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS`. It is never `LIVE_PRESENCE` and never a holder claim. Whether a consumer of the claim accepts it is the consumer's decision. Today the company-claim identity step accepts any current claim, as it does for the other automatic grade, so this route will satisfy it: that is an owner decision to revisit before real use.

## What was built

- **Policy** (`domain/idv_policy.py`): an automatic alternate may carry `face_match: true` and `face_threshold` (0.2 to 0.99, default 0.5). Both are refused anywhere else.
- **Decision** (`domain/idv_decision.py`): on a face route a match against the document's photograph is the only thing that approves, and no machine-readable lines, typed details, document date or liveness result are needed (a known expired date still rejects). Not found, unclear or no match is INDETERMINATE: a retake, never an automatic rejection on a biometric score. `face_signal` turns a similarity into the signal.
- **Claim grade** `FACE_MATCH_NO_LIVENESS` (`application/idv_claims.py`, migration `verification_0036`).
- **API** (`api/idv_cases.py`): `POST /v1/idv/cases/{id}/document-image` takes `selfie_base64` and `consent` on a face route. Both photographs are scanned for malware and re-encoded like any upload, compared in memory by the face sidecar (CR-128), and neither is kept. The person's agreement is required and is recorded as the reason `FACE_CONSENT_GIVEN`.
- **The owner's acknowledgement** (`ZID_IDV_FACE_ROUTE_ACK`): the route refuses every request (`FACE_ROUTE_NOT_ENABLED`) unless it equals `accept-no-liveness-no-evaluation`. The cell logs a warning at start-up when it is set. It follows the pattern of `ZID_OPERATOR_DIRECT_ACK` (CR-035).
- **Web page** (`/account/identity-proofing`): Passport and Aadhaar card both take this route: upload the document, take a live photograph (camera or file), agree, send, and read Verified, Sent for retake or Rejected.

## Rules

- Nothing here is an approval of the face engine. The gate (`engine_gate`) still refuses the engine for the biometric flow, and the Face lab still says it measures and approves nobody.
- Use it only with people who agreed. A comparison of two photographs of a real person is biometric processing: a notice, a lawful basis and an impact assessment are the owners' to settle before anyone but the owner's own test accounts use it.
- To switch it off, delete `ZID_IDV_FACE_ROUTE_ACK` (every face route refuses) or remove the `WEB_FACE` line from a market.

## Not built

- Liveness, authenticity checks of the document, and the evaluation that would let a higher grade be earned.
- A rule that stops the company-claim identity step accepting this grade.

Tests: `tests/unit/test_idv_face_route.py`, `tests/component/test_idv_face_route_pg.py`, `identity-proofing-next.test.tsx`.
