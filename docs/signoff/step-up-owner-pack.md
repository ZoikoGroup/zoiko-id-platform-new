# Owner pack: ZID-ENG-DEC-009 step-up authentication

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Slice: [L-9](../phases/phase-l-second-wireframe-decisions.md); what engineering built: [CR-046](../specs/change-requests/CR-046-step-up-authentication.md).

| # | Ruling needed | Why engineering cannot decide | Reference |
|---|---|---|---|
| 1 | Founder approval of ZID-ENG-DEC-009 and its two trigger tables (the config is generated from them) | The record is proposed, not approved | |
| 2 | Switch `step_up_enforcement` from `shadow` to `enforce`, per environment, once the web flows are tested with real users | It blocks sensitive changes without a proof | |
| 3 | Approve an assurance policy for an S1 enterprise-MFA fallback (TOTP plus a protected password and session), or keep S1 passkey-only (`s1_fallback_mfa`) | The record allows it only under an approved policy | |
| 4 | The challenge lifetime (120 seconds) and confirm the freshness windows (10 and 5 minutes) as Zoiko policy | The record states windows but not the challenge wait | |
| 5 | The rule for what counts as "high-impact" delegated authority, "high-privilege" agent mandates and "high-trust" credentials, and a caller model for break-glass; then the remaining S2 actions (authority grants, mandates, credential issue and revoke, recovery completion, legal-entity changes, OAuth client scopes) can be guarded | The record names the tiers but not what makes an action high-impact | |
| 6 | (Closed by engineering: the session and evidence guards are composed in at start-up through `step_up_hook`.) Confirm the arrangement | An architecture choice | |
| 7 | Password and primary-email change: where they live, and the notify-and-review gate the record asks for | No such flow exists yet | |
| 8 | Federation: request fresh authentication at login (`max_age=0`, `acr_values`, SAML `ForceAuthn`) for each connected provider, and say which providers can supply verifiable freshness | Provider capability and contract | |
| 9 | Risk signals beyond the existing security states (new device, impossible travel, recent recovery, suspect token): sources, privacy review and thresholds | Privacy and signal governance | |
| 10 | Governed recovery for a person with no passkey, and the support escalation path the record asks for | A people and process decision | |
| 11 | Session timeout baselines for privileged workforce contexts (the record proposes 12 hours overall and 30 minutes idle, pending security sign-off) | Security sign-off | |
| 12 | Q10 to Q12: independent approver eligibility, thresholds and solo-tenant exceptions, which S2 combines with | Separate decisions | |
| 13 | The route to action list in `backend/config/step-up/route-actions.yaml` (about 35 routes) is engineering's reading of the table; confirm or change each, in particular which sign-in settings area is a trust policy (`PUT /v1/settings/authentication` only), and session bulk revoke at S1 | The record names actions, not routes | |
| 14 | Switch the platform from shadow to enforce (`ZID_STEP_UP_ENFORCEMENT`) once the owners have read the shadow log; the web client already proves and retries, the operator console does not | Changes what every admin must do | |
