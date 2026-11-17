# CR-110: Provider-hosted biometric methods, rollout, reauthorization, oversight, assisted route and ecosystem use cases (ZID-DTV-WF-04)

Status: open (built and tested; the PostgreSQL component suite was run on a local PostgreSQL 18 for the biometric, privacy, overlay and self-run
tests; the owners confirm the points below). Builds on CR-107, CR-108 and CR-109. The requirement-by-requirement status is in
`docs/traceability/wf04-biometric-register.md`.

## What changes
- **Provider-hosted methods**: eligibility takes `provider`; the provider is the engine and its version is the tenant's ACTIVE pinned model of that
  provider for the method's kind (a provider reporting another version is drift and is held). The verification start, when the profile uses
  biometrics in the country through a provider, needs face match and liveness both eligible; otherwise `BIOMETRIC_METHOD_NOT_ELIGIBLE`, with the
  compliant alternate profiles, under the existing shadow or enforce mode. A self-run start skips this (checked at the chip stage).
- **Decision changed revokes stale claims**: `privacy.register_decision_hook`; the verification module registers at start-up and, when a review
  reversal takes effect, revokes the VALID assertions of the session or assertion the decision names (`DECISION_REVIEWED_<OUTCOME>`).
  `privacy.decision.changed` carries the counts.
- **My reviews**: `GET /v2/privacy/reviews/mine` and a card in the privacy centre (no reviewer identity).
- **Models and fairness (B07)**: `GET /v2/privacy/models` with each version's latest evaluation and a fairness state (NOT_RUN,
  DATA_INSUFFICIENT, PASS_WITH_LIMITS, FAIL, REEVALUATION_DUE; never "pass"); screen to register versions, record evaluations with intervals and
  promote.
- **Transfers and subprocessors (B04)**: list endpoints and a screen to check a transfer, record and decide processor changes.
- **Reauthorization (BIO-20)**: worker step `biometric_reauthorization`: an approved DPIA within 30 days of its end (or lapsed) is flagged once
  (`privacy.dpia.reassessment_due`; migration `privacy_0009`); an ACTIVE model whose evaluation is stale or missing is HELD.
- **Oversight (B12)**: `GET /v2/privacy/biometric-oversight`, counts and what needs attention; Oversight tab.
- **Events**: `verification.biometric.policy_blocked` (a gate refused a method: route, country, purpose, codes, policy hash) and
  `privacy.biometric.deletion_confirmed` (a class became purged).
- **Breach containment (B09)**: dossier `GET /v2/privacy/breaches/{id}/dossier` (case, clocks, capture timeline, audit trail as codes and hashes;
  evidence port `audit_timeline`); processor suspension from the breaches screen.
- **Rollout and signed release decision (B11)**: PRODUCTION_ELIGIBLE has `exposure_percent`; widening is a proposal and a second person,
  narrowing is a step down; people are chosen by a stable hash of scope and person; every approved step up carries `decision_hash` (migration
  `verification_0020`).
- **Assisted route back office (B02)**: choosing an alternative queues the person; staff take and complete it with a reference or a reason code;
  nobody handles their own; no personal reason is stored (migration `privacy_0010`).
- **Ecosystem use cases (BIO-19)**: `config/privacy/biometric-use-cases.yaml`; a Zoiko product's purpose needs its signed integration
  acceptance (gate 9).

## Bugs found by running on PostgreSQL
- An audit detail key containing `biometric` was refused by the evidence ledger's minimization rule; renamed.
- Two outbox events on the same review used the same aggregate version; the second is version 2.
- An untyped SQL parameter in a `CASE WHEN :m IS NULL`; cast.

## Owner decisions (proposed values, none ratified)
1. A provider's method version is its pinned model version in the tenant's inventory.
2. A reversed decision revokes the claims resting on it.
3. The 30-day reassessment window, and holding a model whose evaluation is older than its validity.
4. Rollout by person, widened by two people, narrowed by one.
5. The assisted route's reason codes.
6. The two ecosystem use cases and their purpose codes; their acceptances are COUNSEL until signed.
