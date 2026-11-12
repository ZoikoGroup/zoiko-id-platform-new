# CR-073: Official-register attestations of a provider's certification (ZID-RCG-003 RCG-G02)

Status: open (domain, storage, operator API, audit and the release-freeze check built and tested; no automatic register connector yet; the owners confirm the points below).

## Why
`certification.py` and its configured list say who holds which certification. Document 03 wants the proof: what an OFFICIAL register said, when it
was read, and a hash of the answer, so that "certified" is a checked fact that stops being true when the proof goes stale (T07) and still answers
for a past moment (T08, T46).

## What changes
- **One table**, `verify.certification_attestation` (migration `verification_0007`), append-only, platform configuration. A trigger refuses a
  future snapshot and one not newer than the previous snapshot of the same scope.
- **Domain** (`domain/attestation.py`, pure): a scope is entity, scheme, country and service; a snapshot names the register entry (https URL, entry
  id, certifying body), when it was read, the hash of the response and where the response is kept. `decide` is CERTIFIED only when the latest
  snapshot at the time asked is ACTIVE, in its validity dates, exact in scope (a component never covers its service) and fresh (default seven days).
  Otherwise HOLD, with the reason. Asked about a past moment, it answers from the snapshot that was then the latest. `changes` reports a lost
  certification for exactly the scopes it covered.
- **Store** (`infrastructure/attestation_store.py`): `record` (checks the domain rules and the earlier snapshots), `history`, `decide_scope(s)`,
  `scopes_of`.
- **Operator API** (same router as CR-072): `POST /v1/regulatory/attestations` (capability `regulatory.attestation.record`, one approver) and
  `GET /v1/regulatory/attestations` (scopes and today's decision). Audit: `regulatory.attestation.recorded` (proof hash and status only); a refusal as
  `regulatory.release.refused`.
- **Use.** A market release pins the supplier scopes it relies on at the freeze, which refuses any that are not CERTIFIED; `market_open` re-checks
  them on every request, so a withdrawn or stale certification closes the market without editing any release.

## Owner decisions (proposed values, none ratified)
1. Seven-day freshness for every scheme; a per-scheme limit is supported by the domain and not configured.
2. Snapshots are recorded by a person with the capability until a register connector exists (documents 05.1 and 05.2 name the registers).
3. The preserved response is kept outside this table (`evidence_locator`); only its hash is stored here.
