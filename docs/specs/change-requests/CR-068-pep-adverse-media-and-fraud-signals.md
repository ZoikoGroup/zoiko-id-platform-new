# CR-068: PEP screening, adverse media and fraud/velocity signals (ZID-GAP-001 WP-14; ZID-ED-04 sections 05, 06)

Status: open (built and tested; owners ratify).

## Why
Screening covered sanctions lists only (OFAC, UK, EU, UN, local). WP-14 asks for risk-tiered monitoring: politically exposed persons, adverse media, and
device and velocity signals, each recorded with its list version or rule, its rationale and the review outcome, and none of them making a legal
determination. A hit has to be challengeable and resolvable with evidence, queues and notifications have to fail safe, and a stale feed has to block a
high-risk approval by policy.

## What changes
- **PEP is another list kind** (`ListId.PEP`) through the existing machinery: versioned, hashed, freshness-checked, matched with the existing matcher. A
  PEP entry is a person with a position and a level (`LEVEL:` and `POSITION:` in `programs`, so the reviewer sees why). A PEP match is a `SCREENING_ALERT`
  plus `PEP_MATCH`: the case goes to `REVIEW_REQUIRED` with the list version, score and matching reasons in the signed evidence, exactly like a sanctions
  potential hit. It is never rejected or verified by a rule. A PEP match is not treated as a designation, so it never feeds the OFAC 50 percent or OFSI
  ownership tests.
- **A documented PEP feed format** (`parse_pep`): CSV with the header `source_ref,name,aliases,birth,country,position,level` (aliases separated by `|`) or
  JSON `{"entries": [...]}` with the same fields; `level` is one of `HEAD_OF_STATE, NATIONAL, REGIONAL, LOCAL, INTERNATIONAL_ORG, STATE_ENTERPRISE, RELATIVE,
  ASSOCIATE`. A malformed or duplicated file is refused whole. **No download source is configured and PEP is off by default** (`sources.PEP.url: null`,
  `high_risk_required_lists: []`); the owners must choose a licensed source and convert it to this shape.
- **Stale feeds block high-risk approvals.** New policy key `high_risk_required_lists` (proposed `[PEP]` once a source exists). For an elevated-risk case those
  lists are required in addition to `required_lists`; a missing one, or one older than `list_max_age_hours`, makes the decision `UNAVAILABLE` with
  `LIST_UNAVAILABLE:PEP` or `LIST_STALE:PEP` and `HIGH_RISK_APPROVAL_BLOCKED`. An ordinary-risk case is not blocked by it.
- **Adverse media is a port** (`AdverseMediaPort`, a Protocol) with a recording fake (`FakeAdverseMedia`); there is no real adapter. The rule
  (`apply_adverse_media`) can only add a review or raise the risk tier, never clear or lower anything; `NO_HITS` adds nothing and is not a clearance. A provider
  timeout, an exception or an unreadable result is recorded as a failed check and routes to review (fail safe). The record holds provider, query time, outcome,
  counts and rationale, never the query text.
- **Velocity signals** (`record_attempt`): sign-up, verification and claim attempts are counted per device, contact and network key in sliding windows
  (an event exactly one window old has aged out). A breach opens a review item with the rule, count, threshold and window. The attempt is never blocked silently
  and never auto-approved. Only HMAC-SHA256 digests are stored (`hash_identifier`, tenant-scoped, optional deployment pepper); a value that is not a digest is
  refused (`IDENTIFIER_NOT_HASHED`).
- **Review items** for velocity and adverse-media signals (`screening_signal`, append-only, and `screening_signal_review`): one open item per dedupe key, so a
  repeat does not duplicate. `challenge` keeps the item open (CHALLENGED) with the statement hash and an evidence reference; `resolve` records the outcome
  (`CLEARED_BY_REVIEWER`, `CONFIRMED_RISK`, `ESCALATED`), a rationale of at least a sentence, an evidence reference and the reviewer, and never edits or deletes
  the signal. A notification that fails marks the item `FAILED` and leaves it open in the queue; nothing is cleared by a failure. PEP hits use the existing case
  review and appeal (`review`, `request_appeal`); their evidence reference is the signed evidence row and its hash.
- **Migration** `screening_0004`: `PEP` added to the list-id check; tables `screening_velocity_event`, `screening_signal`, `screening_signal_review` (tenant
  row-level security, registered in `table-ownership.yaml`). No new HTTP route and no published contract change; the new functions are exported through
  `screening/ports.py`.

## Tests
`tests/unit/test_screening_pep_signals.py`: a PEP match goes to review and is not rejected, ownership tests are untouched, a stale or missing PEP list blocks high
risk only, parser formats, adverse media only adds, error and timeout go to review, velocity thresholds and exact window edges, hashing, resolution rules.
`tests/component/test_screening_pep_signals_pg.py` (PostgreSQL): a PEP hit and a velocity breach each open one review with the list version or the counts, a
repeat does not duplicate, a failed notification leaves the item open, and resolution records outcome and evidence and leaves the evidence in place. Existing
screening tests were adjusted only for the new list kind.

## Not done
- **No licensed PEP data source** and no download address; PEP screening is off until the owners choose one and convert it to the documented format.
- **No adverse-media vendor**; only the port and a fake exist.
- **Velocity thresholds are unvalidated** against real traffic. The producers (sign-up, verification and claim code) do not call `record_attempt` yet, nothing
  prunes old `screening_velocity_event` rows or sweeps `FAILED` notifications, and there is no console queue or API route for signal reviews.
- Ownership-graph propagation of PEP or adverse-media alerts beyond the persons already screened, and a feedback loop that tunes thresholds from review outcomes,
  are not built.
- Adverse-media results are not fed into a case's risk tier automatically; `check_adverse_media` returns the effect for the caller to apply.

## Owner decisions (proposed values, none ratified)
1. **PEP source and enablement.** Proposed: a licensed source and `high_risk_required_lists: [PEP]`, so a high-risk approval needs a current PEP list. Alternative:
   add PEP to `required_lists`.
2. **PEP levels.** Proposed: the eight levels above; relatives and associates are listed as levels and matched like any entry.
3. **Velocity table.** Proposed (window, threshold): device sign-up 24h/3, verification 1h/5, claim 24h/3; contact sign-up 24h/2, verification 1h/5, claim
   24h/3; network sign-up 1h/10, verification 1h/20, claim 24h/10; every breach raises to ELEVATED.
4. **Adverse-media timeout and tiering.** Proposed: 10 seconds; an error or timeout is ELEVATED and a review; a hit takes the provider's severity (at least
   ELEVATED).
5. **Signal resolution outcomes** (`CLEARED_BY_REVIEWER`, `CONFIRMED_RISK`, `ESCALATED`) and who may resolve (proposed: a compliance reviewer who is not the
   subject).
6. **Hash pepper.** Proposed: a deployment secret per cell; without one the digests are tenant-scoped only.
