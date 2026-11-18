# CR-113: WF06 screening control plane

Source: Wireframe 06, Global AML, Sanctions, PEP and Adverse-Media Screening (ZID-DTV-WF-06). Status: engineering built 18 Nov 2026. No provider is contracted, no market is signed, and no official download address is configured, so nothing here is switched on anywhere.

## What was built

**Rules (pure domain, `screening/domain/`)**
- `coverage.py`: the four result dimensions of section 05 (coverage, match, disposition, review) and the canonical response of section 11. A class that is not configured reads NOT_CONFIGURED with a null result, never clear. A case clears only when every class the policy requires was checked against a fresh source. The customer summary never says "AML approved", "clean" or "no risk", and a restricted or prohibited case reads like a hold.
- `source_release.py`: the governed release of an official source. Validation quarantines a source that is not registered, a legacy source (the OFSI Consolidated List closed on 28 January 2026), an address off the publisher's hosts, a failed schema, a digest that differs from the advertised one, or a UK file published before the cutoff. Zero rows and a large drift are flags each approver must acknowledge by name. Two approvers other than the stager are needed; a rollback needs two approvals of its own.
- `disposition.py`: maker and checker, Legal-only prohibitions and the freeze, block and report acts, a stale revision refused, an append-only chain an auditor can verify, and appeals decided by someone who did not decide before.
- `provider_controls.py`: a licensed provider is called only when it is approved, Finance authorised the price schedule, the class, market and region are in the contract, the circuit is closed and the quota and budget have room. No failover to another region or provider. A callback is trusted only if its signature, clock window, tenant and event id all hold.
- `monitoring_watch.py`: deduplicated rescreen work, a storm limit, separate last-attempt and last-success marks, and reopening of cases on a delisting or a changed entry.
- `eligibility.py`: default off. A signed market policy, the region, a lawful basis, a current identity claim, the tenant's right to the class and a usable source are all required, and every refusal says why.
- `evidence_export.py`: a purpose-bound, watermarked, expiring, tamper-evident package; sensitive kinds need a second approver and leave only as hashes.
- `matching.py`: a transliteration trace, Devanagari for sanctions screening only (identity matching is unchanged), expired identifiers kept as candidates for a person, and a flag for missing secondary identifiers. `ownership.py` gains the EU ownership-and-control test.

**Storage (migrations `screening_0006` to `0011`)**: governed releases (never deleted, receipt and validation immutable); the screening case with restricted subject details and candidates and an append-only decision chain; supplier calls (the idempotency key and the bill, append-only); provider events (a refused event never claims an event id); evidence packages; ongoing watches and their marks. All tenant-scoped tables are row-level secured; a screening reviewer reads cases across tenants only when the session says so, and every write is under the case's own tenant.

**Routes**
- Operator: `/v1/screening/sources`, `/releases` (stage by fetching, approve, publish, approve-rollback, rollback), `/providers` (read only), `/review/queue`, `/review/cases/{id}` (audited), propose, finalise and withdraw, evidence exports (request, approve, fetch) and watches (create, approve, activate, status). Twelve operator capabilities with four separation-of-duties rules.
- Tenant: `/v1/screening/eligibility` and `/cases` (OWNER or ADMIN; the identity claim and the region are the platform's, never the caller's).
- Provider callback: `/v1/integrations/screening/{tenant}.{provider}/events`.

**Screens**: the operator console pages Sanctions sources, Screening cases (queue, investigation, four answers, candidates, proposals, decisions, evidence package, watch) and Screening providers; the customer page Screening.

**Worker**: the `screening_watches` step re-screens due watches, only where ongoing monitoring is approved for the market.

## How it is proved

Unit tests for each rule module and for the registries; component tests on PostgreSQL for the governed release, the case flow, licensed providers, the reviewer and customer APIs, evidence exports, provider callbacks and watches; web and operator logic tests and typechecks.

## Not built or not proved

- No provider adapter ships; a contracted vendor is needed for each, and the licensed path is tested only with a recording fake.
- The config lists no official download address, no market and no provider.
- The watch's "release gates signed" setting defaults to off.
- Nothing has been run in a browser, with assistive technology, under load, or through an outage on a deployed cell.
- Legal and AML sign-off, the applicability matrix, independent match-quality evaluation, reviewer training and G0 to G3 approval are people's work.
