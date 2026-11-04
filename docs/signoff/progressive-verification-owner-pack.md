# Progressive verification: what only the owners can close (ZID-EDR-006, ZID-EDR-005; slices L-6 and L-5, CR-056)

1. **Founder approval** of the decision records and of `backend/config/verification/tier-policy.yaml` (the tiers, the optional checks and their reasons, the alternatives).
2. **What counts as R1.** Built as an identity assertion at IAL-Z2 (a document, a live selfie and a binding, or an accepted equivalent). Confirm that this is the proofing standard for "Identity verified", or name another.
3. **What counts as R2.** Built as an organization in the VERIFIED state (company check plus a reviewer-confirmed mandate, CR-052) and a passkey session. Confirm, in particular that mandate evidence and the company check are one state today.
4. **Who needs which tier for what.** The policy says what each tier is, not which actions need which. Rule which features (for example inviting an Admin, exporting evidence, changing the legal entity) need R1, R2 or R3.
5. **The reasons that make an address required** (statutory, regulated, contract, documented risk) and, per country, which of them applies. Until a country rules, an address is never required.
6. **Phone**: confirm it is optional and only for workflows that depend on possession, and that no phone code is accepted as identity proof or as sole authorization of a critical change.
7. **The accessibility alternatives.** ASSISTED_PROOFING needs an operating procedure, trained staff, a booking route and a record of what the person showed; EQUIVALENT_CREDENTIAL needs a trust-policy mapping per issuer (validity, revocation, binding, jurisdiction). Engineering has shown them as named processes and built nothing behind them.
8. **Consent and biometric notices** per jurisdiction (EDR06-07): the counsel-approved texts and the lawful basis, and the non-biometric alternative.
9. **Accounts made before sign-up** have no email proof on record and show "No level yet". Rule how they prove an inbox (a confirmation message, an invitation, or leave them as they are).
10. **Provider polling.** Veriff and Sumsub sessions are polled after ten minutes, at most every ten minutes. Confirm the delay and interval, and confirm against a live Sumsub account that `/resources/applicants/{id}/one` returns `review.reviewStatus` and `review.reviewResult` as the poll reads them (an unrecognised answer decides nothing).
11. **Resubmission**: how many times a person may be asked to resubmit before the session ends, and what the person is told.
