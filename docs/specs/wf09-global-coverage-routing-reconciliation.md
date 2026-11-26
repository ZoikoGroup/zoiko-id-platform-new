# WF09 (Global coverage, provider routing and orchestration): what already exists, what CR-171 added, what is left

Written 25 Nov 2026 for the Zoiko iD product owner. ZID-CP-WF09 v2.0 asks for a governed capability registry and a deterministic routing resolver, with no second workflow engine and no second public verification outcome. This is the reconciliation from reading the code, and what was built. It makes no coverage, certification, procurement or launch claim.

**Headline.** The platform already had the legal and contractual spine of WF09: market releases with seven evidence gates and four approvals, certified-provider attestations, provider entitlements with a dry-run router, an exact-scope provider kill switch, partner entitlement statements, vendor cost records, a provider lab and Veriff and Sumsub translators. What it did not have is the thing WF09 is about: a registry of **provider capability per method**, a **deterministic resolver that records why**, a **provider attempt state machine that refuses to retry an unknown outcome elsewhere**, a **preflight that shows who loses coverage**, a **release with independent approvals of the exact dependency set**, and a **public number derived from released cells only**. CR-171 builds those, inside the `verification` module, and leaves everything above in place.

## Reconciliation by work package

| WF09 work package | Already in the repo | Status | CR-171 / what is left |
|---|---|---|---|
| E01 reconcile WF-03 and the Verify API | Verify API sessions; WF-03 workflow compile in `verification/workflow_service` | Built (CR-172) | The resolver sits beneath both and authors nothing. **It now looks up** whether the named workflow version is APPROVED (WF-03's own table) and which methods the company is entitled to (the company's live provider entitlements). A caller can no longer assert either: the request models refuse those fields |
| E02 market and document capability registry | Market releases (`market_release.py`), country registry, offerings coverage report | Built (CR-171) | `route_capability`: per provider and method, versioned, append-only, with a manifest digest. Cells are counted from approved, current capabilities and an open market. Document specimens are listed in the manifest by country; **nothing yet loads a real specimen catalogue** |
| E03 supplier contract and qualification manifest | Certification attestations, partner entitlement statements, kill switch | Partly | Approval needs a second person, a qualification date, a contract date and a signed legal determination reference with its own expiry (the database checks it). **No reseller-rights record, no signed change log, no secrets or rotation record** |
| E04 regional processing and transfer graph | Residency profiles, privacy gate | Built (CR-172) | A versioned data-flow graph per provider (ingress, transit, storage, remote support, analytics, telemetry, backup, onward subprocessors, each with region and data class). Every hop must sit in an allowed region, and the legal determination names the graph's digest: **any change to the graph stops routing to that provider until a second person approves a capability version covering the new graph**. The change is announced and audited with what was added. **Detecting a change at the provider or cloud itself still needs a feed; today a person records the new graph** |
| E05 deterministic resolver | `provider_adapter.route` (country, service, purpose, region, assurance, cost rank) | Built (CR-171) | Eight hard gates in the document's order, every failing reason named, then risk-adjusted cost per valid outcome (a vendor's own claim is ranked at half), then latency, then provider id. Same snapshot, same bytes. The old dry-run route stays |
| E06 provider attempt state machine and idempotency | Callback dedupe, vendor cost records | Built (CR-171) | Nineteen states, forbidden moves refused by name, one attempt per idempotency key (a unique index), a timeout after dispatch is UNKNOWN, an unknown outcome is reconciled and never sent to another vendor, only the owning verifier finalises |
| E07 one certified pilot cell | none | **Not built** | Needs a real provider, a contract and a sandbox. **No provider adapter was written for this** (the existing Veriff and Sumsub translators are untouched) |
| E08 coverage and route audit, WF-08 evidence | Evidence ledger and receipts | Built (CR-172), as audit records | Every decision, no-route, attempt reservation or cancellation, state change (an unknown outcome is its own E3 event), capability change, data-flow change and release publication or rollback writes an outbox event (`route.selected`, `route.no_route`, `route.attempt.*`, `provider.capability.changed`, `provider.data_flow.changed`, `route.release.*`) and an audit record in the same transaction; the trace shows them. **They are audit records on the ledger, not WF-08 decision receipts** (a receipt belongs to a decision of the platform's policy engine); a replayed request writes nothing more |
| E09 permissioned coverage dashboard | Operator offerings view | Built (CR-171) | A tenant console page `/admin/routing` with six tabs. Tenant scoping is by the tenant transaction; capability tables are platform-wide, so a company sees provider facts. **Commercial terms are not shown, but no test proves one company cannot infer another's** |
| E10 public claims pipeline | Offerings catalog, regulated claims gate | Partly | `GET /v1/routing/claims` derives the number from released cells and refuses when any cell lacks approved evidence. **Nothing publishes it to a public page or sales collateral** |
| E11 alerting and runbooks | Operations incidents | Not built for routing | Needs real providers and traffic |
| E12 second provider and safe handoff | none | Partly | The failover plan, re-route and the applicant messages for each state exist (a switch before dispatch, a confirmed not-processed, explicit recapture); **no second real provider** |
| E13 preflight simulation | Provider lab, policy simulate | Built (CR-171) | Reads only; shows who loses coverage and who switches provider; blocks a release whose required group loses coverage. **No cost projection yet** |
| E14 performance and unit economics | `commercial/vendor.py` costs | Partly | Invoice lines are matched to the attempt ledger and anything odd is held. **No SLI instrumentation**; observed, contractual and marketed times are separate fields |
| E15 provider health and quarantine | Kill switch, circuit breaker for equivalence groups | Built (CR-172) | A breaker computed from this platform's own dispatched attempts (30 percent unknown or bad within 15 minutes, never on fewer than 20; half-open after five quiet minutes); a quality quarantine when fewer than 70 percent of the last week's results are valid (never on fewer than 50), independent of uptime; a per-minute cap a provider's manifest may state, so a failover burst cannot overload the one left. **The thresholds are defaults nobody has ratified, and nothing alerts a person when a breaker opens** |
| E16 market expansion pipeline | Country opening | Existing | No change |
| E17 independent adversarial and accessibility review | none | **Not built** | People |

## What CR-172 added (25 Nov 2026)

- **Trusted inputs**: workflow approval and entitled methods are looked up (see E01). The idempotency fingerprint is now the request itself, so a later change in the platform's own facts never reads as a reused key.
- **Entitlement per provider and method**: the company's own provider entitlement is checked through the existing entitlement guard; its reasons appear as `ENTITLEMENT:...` at the qualification gate.
- **Data-flow graph** and the rule that a changed graph stops routing (see E04); migration `verification_0038`.
- **Evidence** for every routing fact (see E08), and the trace lists the audit records written for a decision.
- **Breaker, quality quarantine and throttle** (see E15).
- **Applicant messages**: one safe next step for every state, never the provider's name, never "failed" for a technical problem, and a plain statement of whether the evidence already given is kept.
- **Console**: data-flow viewer and recorder, evidence list on the trace, the applicant message on each attempt.

Still not done and not buildable here: **a real provider and a pilot cell (E07)**. That needs a contract, credentials and a sandbox from a provider; no adapter or stub was written.

## What CR-171 built

- **Domain** (`verification/domain/routing.py`, pure): hard gates and the resolver; the attempt state machine and failure classes; failover plan; coverage cells, counts and the claim guard; release approvals; cost reconciliation; SLO fields.
- **Persistence**: migration `verification_0037` with five append-only tables (`route_capability`, `route_decision`, `route_attempt_event`, `route_release`, `route_release_approval`). Decisions and attempt events are tenant-isolated by row-level security. A database check keeps the second-person rule for an approved capability.
- **Routes** (`/v1/routing/...`, three new catalogue actions `verification.routing.read|manage|approve`, route-map entries): capabilities, coverage, claims, resolve, decisions, attempts (reserve, advance, fail, reroute), preflight, releases (draft, preflight, approve, publish, roll back), reconcile.
- **Kill switch**: the existing exact-scope provider suspension turns a provider SUSPENDED in the resolver at once.
- **Console**: Coverage, Providers and data paths, Simulation, Trace and recovery, Releases, Claims and cost.
- **Tests**: 17 rules, 8 database cases, console logic.

## Decisions the code makes on purpose

- A missing route is NO_ROUTE with a reason, never an applicant failure.
- A provider's own claimed speed and success rate are never ranked as measurements.
- Routing selected, provider submitted, result pending and the final outcome are four separate states.
- A published release does not change a decision already recorded or a session already running; a decision keeps the route version it was made under.
- Reserving an attempt re-checks legality, contract and quota at that moment. An attempt that no longer passes is cancelled and no vendor call can follow.

## Not claimed

No coverage count is a launch claim. No provider is qualified or contracted. Nothing here was run against a real provider, and no conformance, accessibility or security review has been done by anyone other than the author.

## Left to do

- **A real provider and a pilot cell** (E07), then a second one (E12).
- **A feed that detects a provider's subprocessor or cloud-region change** (today a person records the new graph), and a WF-08 decision receipt, if the owners want one, instead of audit records.
- **Reseller rights, secrets and rotation** records for a provider.
- **SLI instrumentation and an alert** when a breaker opens or a method is quarantined; ratify the breaker, quality and throttle thresholds.
- **Showing the applicant message in the applicant journey** (WF-01): the message is produced and served by `GET /v1/routing/attempts/{id}/applicant-message`, but nothing in the applicant screens calls it yet.
- **Break-glass** record and post-event review for an emergency suspension.
- **Cost projection** in the preflight, residence-country gating, a real document specimen catalogue, and a public publication of the claim number.
- **Concurrency test** of ten parallel retries, and a negative test that one company cannot infer another's terms.
