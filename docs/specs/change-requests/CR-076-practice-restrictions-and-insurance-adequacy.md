# CR-076: Practice restrictions and insurance adequacy (ZID-RCG-003 RCG-G05)

Status: open (domain, configuration, engagement hook, wording and the Zoikorum deny/review fixtures built and tested; no rule is published, so nothing changes until the owners publish one; the owners confirm the points below).

## Why
The credential module already decides whether a person holds a live licence in the right place for a service (`eligibility.py`), whether cover names
the person, territory, dates and work (`standing.insurance_result`) and whether a confirmed adverse finding restricts them. It does not read what a
licence's CONDITIONS mean for one service (they are carried as free text and ignored), nor whether cover is ADEQUATE (limit, practice class,
claims-made retroactive date), and it never returns the split document 10.1 requires: QUALIFICATION_VERIFIED together with
PRACTICE_ELIGIBILITY_NOT_ESTABLISHED, not one composite VERIFIED. Tests T13 to T17 and the mandatory counterexample need that.

## What changes
- **Domain** (`credential/domain/practice.py`, pure). A `PracticeRule` per eligibility policy lists which licence conditions are `permitted` (do not
  limit the service), `review` (need a person) or `blocking` (close the service), matched after folding case, spaces and punctuation. A condition the
  rule does not list is REVIEW (`CONDITION_NOT_CLASSIFIED`), never permitted. An `InsuranceRequirement` states a minimum limit and currency, accepted
  practice classes and whether claims-made cover needs a retroactive date.
  `insurance_adequacy` adds to the existing checks: limit not reported, currency mismatch (never converted), limit below minimum, practice class not
  covered, cover basis not reported, retroactive date missing or after the start of the work.
  `decide` keeps qualification and practice apart and returns the reasons `QUALIFICATION_VERIFIED` and `PRACTICE_ELIGIBILITY_NOT_ESTABLISHED`.
  `apply_to_engagement` can keep an engagement result or make it stricter (NOT_ESTABLISHED blocks, REVIEW holds); it never upgrades one.
- **Configuration** `backend/config/credential-trust/practice-rules.yaml`, loaded by `registries.find_practice_rule`. It ships with NO rule (one example
  is a comment). A malformed rule, a condition in two classes, a non-positive limit or a duplicate version is refused whole.
- **Engagement hook** (`trust_service.evaluate_engagement`, new optional `practice` argument, otherwise the rule is looked up by policy id). With no
  rule the engagement is decided exactly as before. With one, the licence's conditions and the cover's adequacy are applied and every failing reason
  is returned.
- **Facts.** The insurance claim spec and `insurance_facts` carry `coverage_limit`, `currency`, `practice_class`, `cover_basis` and `retroactive_date`
  when the source confirms them; absent from the source means absent here, never filled in.
- **Wording.** Twelve reason codes, each with plain words in English, Spanish and French (`reasons*.py`; the existing drift guard covers them).
- **Fixtures.** Unit: T17 (region, class, limit, currency, claims-made), the counterexample, never-upgrade, parsing. Database, through the real
  engagement path with the simulator source: a blocking condition, a review condition, an unclassified condition, a permitted condition, T16 (a licence
  from another state is not practice here), T17, and "no rule changes nothing".

## Not in this change
- **No real rule is published.** Which conditions close which service, and what limit is adequate, are decisions for Zoikorum product, Legal and
  Insurance (document 12, item 785).
- Supervision, employment and authorization rules beyond licence conditions (document 06 names them in the rule pack); conditions are the first slice.
- A live regulator or insurer connector; the simulator source is the only one today.
- The practice split is returned as reason codes on the existing engagement result; a dedicated field in the API contract is a separate change.

## Owner decisions (proposed values, none ratified)
1. An unclassified licence condition is REVIEW, not permitted.
2. Cover in another currency never meets a limit; conversion is a business decision, not made here.
3. A claims-made policy needs its retroactive date on or before the start of the work.
