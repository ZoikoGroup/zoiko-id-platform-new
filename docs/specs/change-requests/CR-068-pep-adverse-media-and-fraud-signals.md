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

## Wired into the journeys, with a reviewer queue (second slice)
- **Three journeys now count themselves** through one best-effort call, `screening.note_attempt`: a person sign-up (`POST /v1/registration/intents`, in the
  registration tenant), a verification start (`POST /verifications`, counted before the country gate, so a refused attempt still counts) and a company claim
  (`POST /v1/org-claims`). The call hashes the contact, the client network (kept as its /24 or /48) and the device id (`X-Zid-Device`, an opaque id the web app
  keeps for the browser; a client can omit or change it, so it is advisory). For a verification or a claim the contact key is the person's stable id, not an
  address. It runs in a savepoint and never raises: a failure is logged and the journey goes on exactly as before, and a breach never blocks.
- **A hash pepper per cell**: `ZID_SCREENING_SIGNAL_PEPPER_FILE` (a file, read once at start-up and never logged). Without it the hashes are tenant-scoped only.
- **Reviewer routes** (`/v1/reviews/signals`, an operator-plane route like the other `/v1/reviews/*` routes): `GET` the queue (everything not yet resolved, oldest
  first, including items whose notification failed), `POST .../challenge` and `POST .../resolution`. Two catalogued protected actions,
  `verification.screening.signal.read` and `verification.screening.signal.review` (R2/E2): a tenant role never grants them. A resolution needs an outcome from
  `CLEARED_BY_REVIEWER`, `CONFIRMED_RISK`, `ESCALATED`, a reason of at least a sentence and an evidence reference; the raised signal is never changed.
- **A console screen** in the operator console (`/signals`, nav entry beside Post-reviews): the queue with risk tier, source, rule, plain-language summary,
  status and notification state (a FAILED notification is shown as a warning), a Review dialog with separate Challenge and Resolve forms, and a short guide for
  reviewers (`docs/operator/signals-review-guide.md`). No raw identifier is ever shown.
- **Housekeeping**: worker step `signal_housekeeping` drops attempt rows older than eight days (longer than the longest 24-hour window); signals and review
  items are never pruned. `retry_failed_notifications` exists for when a notification channel is chosen.

## Tests (second slice)
`test_screening_signals_api_pg.py` (PostgreSQL, over HTTP): three sign-ups from one address open exactly one signal, all three answer 202 and no address appears in
any stored value; the queue, a too-short and a proper challenge, bad and short rulings, a valid ruling that records the reviewer, a repeat (409), an unknown id (404),
a signed-in person without the reviewer actions (403) and no token (401); old attempt rows are pruned while the signal stays; a claim and a verification each call the
hook with the right tenant, contact and device. The existing registration suite still passes. Console: 10 tests (queue, FAILED warning, empty state, shortened
hash, challenge and resolution bodies, an API error).

## Shadow mode, a notification channel and the device id (third slice)
- **Shadow mode is the default** (`ZID_SCREENING_VELOCITY_MODE`: `off`, `shadow` or `review`). In `shadow` the attempts are counted and each first crossing of a
  threshold is written to the audit trail as `shadow_breach` (rule, count, threshold, window and the key hash), with no review item and no notification, so the
  owners can read what the proposed thresholds would have flagged on real traffic before anyone is asked to review it. `review` opens an item per breach;
  `off` records nothing. Change the mode only after the shadow trail has been read.
- **The platform channel is the notification.** A raised item publishes one catalogued outbox event, `screening.signal.raised` (review id, source, rule, tier; no hash,
  count or name; in `proposed-events.yaml`), which any consumer (a webhook, a future alert or email) can act on. A delivery that fails leaves the item open and marked
  `FAILED`; the worker step `signal_housekeeping` retries it and a second sweep finds nothing to do. Exactly one event per item.
- **The web app now sends the device id.** The BFF keeps a first-party cookie (`__Host-zid_device`, 128 random bits, HttpOnly, one year) and forwards it to the cell as the
  header `X-Zid-Device`, always deleting any such header the browser sent, so the BFF is the only source. It is covered for the cell-cookie relay, the bearer relay and the
  public registration calls; the operator app is untouched. `ZID_DEVICE_COOKIE=off` turns it off. The privacy owner's note is `docs/privacy/device-cookie.md`; the legal
  basis and the notice text are for the DPO and Legal.

## Not done
- **No licensed PEP data source** and no download address; PEP screening is off until the owners choose one and convert it to the documented format.
- **No adverse-media vendor**; only the port and a fake exist. Nothing calls `check_adverse_media` from a live journey yet, because there is no provider to ask.
- **Velocity thresholds are unvalidated** against real traffic. The default mode is now `shadow` for that reason; nobody has read a shadow trail yet.
- **Nothing consumes `screening.signal.raised` yet.** The event is published and retried; an alert template, an email or a webhook that acts on it is a separate design
  (the alert catalogue and the email estate are fixed lists). Until then the operator queue is where a reviewer looks.
- **The device cookie** needs the DPO's and Legal's decision on basis and notice before it is relied on in production. The BFF's `reauthorize`, `callback` and `logout`
  calls do not send the header (they are not attempts).
- **The operator console screen** was built and tested against mocks and the real response shapes, and the app builds with the `/signals` route; it was not clicked
  through with a live reviewer session, which needs the operator's own credentials and a passkey.
- Ownership-graph propagation of PEP or adverse-media alerts beyond the persons already screened, and a feedback loop that tunes thresholds from review outcomes,
  are not built.
- Adverse-media results are not fed into a case's risk tier automatically; `check_adverse_media` returns the effect for the caller to apply.
- The audit event type `verification.screening.signal` is not in the asyncapi catalogue.

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
7. **Where signals are reviewed.** Proposed: the operator console, by the same reviewers who rule on screening cases. A company-side view is not built.
8. **Retention of attempt rows.** Proposed: eight days. Signals and review items are kept like other screening evidence.
