# CR-075: Issuer and accreditation history with provenance (ZID-RCG-003 RCG-G04)

Status: open (domain, storage, operator API and audit built and tested, and the live claim checks read the ledger; the owners confirm the points below).

## Why
`credential/domain/trust_graph.py` already judges an issuer: former names, accreditation at the award date (not today), recognition rules, an unknown
issuer is untrusted. Its registry is a configuration file of sample rows, so nobody can say who stated an issuer's authority or an accreditation
interval, from which official source, when, or what the platform believed at the time of an earlier check. Document 06 asks for an issuer trust
history with its time interval, and an issuer-at-award-time test.

## What changes
- **One table**, `cred.issuer_statement` (migration `credential_0012`), append-only, platform configuration (no `tenant_id`, no row-level security,
  registered in `backend/database/table-ownership.yaml`). One row per dated statement about one issuer: the full issuer record in the trust graph's own
  shape, the https source, the hash of what the source returned and where it is kept. Sequence numbers rise by one per issuer, each statement is newer
  than the one before, none is from the future, none is updated or deleted.
- **Domain** (`credential/domain/issuer_history.py`, pure): `check_statement` (the record must parse as exactly this issuer; source, proof hash and
  evidence required; an issuer trusted for any claim needs a recorded OFFICIAL authority); `known_issuer` and `registry_as_known_at` (what the
  ledger knew at a past moment, layered over the configured registry); `award_check` (issuer at award time: PASS only for an accredited, official,
  non-revoked issuer for that claim type at the award date; unknown, lapsed or unrecorded is REVIEW with reasons).
- **Corrections stay visible.** A later statement may add facts and may end an accreditation or name going forward. Removing or shortening what an
  earlier statement said covered a past date, removing a name, or moving or backdating a revocation is refused as an update; it is accepted only as a
  CORRECTION with a reason, both statements stay, and the changes are reported.
- **Store** (`credential/infrastructure/issuer_history_store.py`).
- **Operator API** (`credential/api/regulatory_issuers.py`, so the credential module needs no new dependency from `operator_access`; the shared
  audit-and-capability wrapper is `zid_platform/operator_actions.py`, which the release routes now use too): `POST /v1/regulatory/issuers` (capability `regulatory.issuer.record`, two approvers),
  `GET /v1/regulatory/issuers`, `.../{issuer_id}/statements`, `.../{issuer_id}/award-check` (judged for an award date with what was known at `known_at`).
  Audit `regulatory.issuer.recorded` in the same transaction: seq, kind, source hash and the kinds of change; never the record, a program or a body.

- **Connected to the live checks.** Every credential API path that read the configured registry (issuer resolution and trust, eligibility,
  engagement, claim checks, disciplinary checks, outreach, the v2 eligibility and issuer-check routes, the registry view) now reads
  `registries.current_issuer_registry(conn)`: the configured registry with the ledger layered over it, read per request in one query, so every server
  process sees a new statement on its next request. With no statements it is exactly the configured registry. A test records an issuer and sees a
  live claim check trust it, then records a backdated revocation as a correction and sees the trust go.

## Not in this change
- Real issuers, accreditors and regulators: none is recorded here; the owners and Legal supply them.
- The event `credential.issuer_status_changed` (needs an event-catalogue entry).

## Owner decisions (proposed values, none ratified)
1. The ledger lives in the credential module next to the trust graph, not in verification.
2. Two approvers to record a statement, as for packs and entitlements; who holds `regulatory.issuer.record` is a business decision.
3. A correction needs a reason of at least ten characters.
