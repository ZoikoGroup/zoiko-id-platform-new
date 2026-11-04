# CR-056: Progressive verification, resubmission and provider reconciliation (ZID-EDR-006, ZID-EDR-005), slices L-6 and L-5 (second part)

Status: open (built). The decision records are proposed and await founder approval. Every tier, reason and alternative in `config/verification/tier-policy.yaml` is a proposal.

## What changes
- **The tiers as data** (`backend/config/verification/tier-policy.yaml`, validated on load by `verification/domain/tiers.py`): R0 basic ("Not identity verified": the email address, no ID, selfie, address
  or phone), R1 proofed person (a validated document and a live selfie, or an accepted equivalent), R2 verified authority (R1 plus a separate company check, mandate evidence and strong authentication),
  R3 critical (R2 plus a fresh strong sign-in and an independent approval). A policy that asks for more at registration, lets a lower tier need more than a higher one, or lets a phone code, an email
  confirmation, single sign-on, a selfie alone or domain control count as proof does not load.
- **The person's tier from facts, never signals** (`application/tiers.py`): email proof from the sign-up record, an identity assertion at IAL-Z2 (the profile that needs a binding and liveness),
  an organization in the VERIFIED state (which has its representative's authority confirmed by a reviewer, CR-052), and the session's authentication level. A higher tier is never reached by skipping
  a lower one, and proofing alone grants no owner, director or approval right.
- **What is asked, and why** (`required_checks`, `check_optional_request`): an address only for a statutory, regulated, contract or documented-risk need; a phone only when a workflow depends on possession
  and it never proves identity; asking for either without such a reason is refused. An address a person typed is shown as "Entered by you, not verified".
- **An accessible alternative that lowers nothing**: ASSISTED_PROOFING and EQUIVALENT_CREDENTIAL are named processes (a person is involved), shown only to someone who says they cannot use the remote check.
  Until one is completed the person stays at the tier they have, and completing it gives the same tier.
- **Claims that explain themselves** (`claim_view`): method, source, policy (level, profile, version), scope, issued, valid until and revocation state for every claim.
- **Routes** (both own-account reads, exempt in `route-map.yaml`): `GET /v1/verification/tier` and `GET /v1/verification/requirements`. Web: a "Your verification level" card on the Identity verification page
  with the next tier's needs, the alternatives and the claims.
- **RESUBMISSION_REQUIRED is its own session state** (verification migration 0004): the provider asked the person to send the evidence again. The session stays open, can expire, and the provider's next result
  moves it on; it is never a decision. The web page says what to do.
- **Polling reconciliation of a delayed webhook** (`service.reconcile_delayed`, worker step `verification_reconcile`): a Veriff session still waiting after ten minutes is asked about (at most every ten minutes
  per session) and the provider's decision is applied exactly as a signed callback would be (same ingest, same deduplication). A missing decision, a failed call or a different transaction decides nothing.
  Sumsub is polled through the applicant call the platform already makes (`/resources/applicants/{id}/one`): a missing or unrecognised `review`, or one that is not finished, is no decision; its response shape is not yet confirmed against a live account.
- **Also in this change (slice L-14):** boundary and rounding tests for the discount bands (CL03-T01, T02, T12) and eight acceptance rows linked.

## Not changed
No new action in the action catalogue, no new outbox event, no change to the proofing profiles or the provider adapters, and no stored document, selfie or template.

## Not built, and why
The assisted-proofing and equivalent-credential processes themselves (an operating procedure and, for the credential, trust-policy mappings per issuer); consent and biometric notices by jurisdiction (EDR06-07 needs the counsel-approved texts); a way for accounts made before sign-up to prove their email; live sandbox runs per country (EDR05-01).

## Tests
`tests/unit/test_verification_tiers.py` (28), `tests/unit/test_verification_resubmission.py` (4), `tests/component/test_verification_tiers_pg.py`, `tests/component/test_idv_reconcile_pg.py` (Veriff and Sumsub), web `verification-tier.test.ts`.
EDR06-01 to 06, 08, 09 and 10 are linked as in progress.
