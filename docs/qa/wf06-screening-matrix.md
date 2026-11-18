# WF06 screening (ZID-DTV-WF-06): what exists, what is open

Prepared 18 Nov 2026 by engineering, from `Zoiko_iD_Wireframe_06_...docx` and the repository. A reconciliation, not an approval. Evidence scale: E1 code referenced, E2 built and tested here, E3 proved on real inputs, E4 operated. **Nothing here is above E2.** No provider is contracted, no market is signed, and no official download address is configured, so screening is off everywhere.

Companion: `CR-113`. Test files: `U` = `tests/unit/test_wf06_*.py`; `C` = `tests/component/test_screening_*_pg.py`.

## Engineering register (section 18)

| ENG | Status | Where |
|---|---|---|
| 01 signed per-tenant, per-market eligibility, default off | Built | `eligibility.py`, `market_policies.py`, `config/screening/market-policies.yaml` (ships empty); U eligibility, C requests |
| 02 subject references from WF01, WF02, WF03 | Partly | a person's current identity assertion and the company's own grade are read by the platform, never the caller; passport-specific assurance is not distinguished from any other valid assertion |
| 03 official source registry | Built | `source_release.REGISTER`; the legal sign-off on each source is people's work |
| 04 secure importer, provenance, quarantine, atomic release | Built | `source_release.py`, `source_releases.py`, migration 0006; U source release, C release and sources API |
| 05 source schema mapping with history | Existing and partly | the OFAC, UK, UN and EU parsers existed; a release comparison (added, removed, changed) is new; golden fixtures per source version are not |
| 06 matching and calibration | Built, not evaluated | transliteration with a trace for Cyrillic, Greek, Arabic and Devanagari; Chinese and other scripts are compared as written; the independent evaluation is people's work |
| 07 canonical case and four result dimensions | Built | `coverage.py`, `screening_requests.py`; U coverage, C requests |
| 08 hold, restricted, legal routing | Partly | holds, restriction, prohibition, Legal-only freeze, block and report with a legal checker are built; a licence or exemption workflow is not |
| 09 restricted queue and investigation workbench | Built | operator routes and the Screening cases page; C cases API |
| 10 licensed PEP and associates, enhanced due diligence | Partly | the port, gates, billing, routing to EDD and the information-request workflow (storage, routes, screens, worker expiry; CR-121, CR-122) are built and tested with a fake; no vendor adapter exists; the customer's answer carries no content |
| 11 licensed adverse media | Partly | same as 10; correction and retraction handling is not built |
| 12 ownership and control graph, rule packs | Partly | the OFAC 50 percent, OFSI, EU and UK rules are built and tested, and a company's owners roll up behind a tenant route that reads a restricted owner as in review (CR-120, CR-122). Nothing reads the WF02 analysis itself or opens the owners' cases automatically, and no screen calls it |
| 13 provider governance | Built | `provider_controls.py`, the registry file, a read-only view; changes are a reviewed change to the file, not a click |
| 14 monitoring watches, batch and event rescreening | Partly | watches, two approvers, attempt and success marks and the worker step are built; list-release events do not yet queue rescreens (only the schedule does) |
| 15 result minimization and tenant-safe API and webhooks | Partly | the minimal status, tenant isolation and signed inbound callbacks are built; outbound customer webhooks for screening decisions are not |
| 16 audit chain, manifest, tamper-evident export | Built | `disposition.verify_chain`, `evidence_export.py`; C exports |
| 17 retention, rights, deletion, vendor deletion attestation | Partly (E2) | the screening data is in the privacy data map (CR-116): found, exported with the result withheld, scrubbed in place; legal holds and schedules come from the privacy module. No schedule is set, the decision chain keeps reviewer rationale, and there is no vendor attestation; C screening privacy |
| 18 reviewer training and runbooks | People | |
| 19 performance, accessibility, attack and outage certification | Not done | nothing has been run in a browser, with assistive technology, under load or through an outage |
| 20 G0 to G3 release approval | Built (E2); no gate is signed | `release_gates.py`, `gates.py`, `/v1/screening/gates`, migration 0012: ten named roles sign each gate once, an open P0 defect or a hold stops a module at once, a hold is released by someone else, a signature is withdrawn only by its signer. The customer API fails closed with no signed gates; watch activation and the watch pass check them. U release_gates, C gates API. **Nobody has signed**: that is people's work. No operator screen yet |

## QA register (section 19)

Covered means a test here shows it. Not wired means the rule exists and is tested but nothing calls it yet.

| QA | Status | Evidence |
|---|---|---|
| 001 OFAC validates against publisher and schema | Covered (rules) | host pinning and schema checks (U source release); a live download has not been run |
| 002 UN reference and measure category kept | Partly | the reference is kept; the measure category is not shown |
| 003 EU corpus version and legal hierarchy note | Partly | the version and digest are kept; the legal hierarchy note is not |
| 004 UK OFSI list rejected, current list used | Covered | U source release |
| 005 blocked download is a network failure, never PASS | Covered | existing fetch tests; a source never fetched reads unavailable |
| 006 truncated file quarantined, corpus kept | Covered | U, C release |
| 007 checksum mismatch or off-host redirect fails | Covered | U source release; the alert on a refusal is the audit record |
| 008 zero rows and large drift need a second person | Covered | U, C release |
| 009 duplicate and racing publications | Covered | C release (concurrent publish) |
| 010 stale source blocks clearance | Covered | U coverage, C requests |
| 011 rollback restores a snapshot and re-evaluates cases | Partly | rollback needs two approvals and is tested; re-evaluating cases is not wired |
| 012, 013 name plus conflicting birth is a candidate; common names | Covered | U matching, U coverage |
| 014 transliteration tested and traced | Covered | Cyrillic, Arabic, Devanagari traced; Chinese is compared as written and the trace says so |
| 015 original spelling preserved | Covered | U matching |
| 016 entity cannot match an individual-only entry | Covered | U matching |
| 017 expired identifier is not a confirmed identity | Covered | U matching |
| 018 missing identifiers are not negative evidence | Covered | U matching, U coverage |
| 019 delisting generates a case change | Covered | publishing a release runs the follow-on (`_follow_on` in `source_releases.py`): cases reopen on a delisting and affected watches are marked for rescreening; C follow-on |
| 020 false positive needs rationale and approval | Covered | U disposition, C cases API |
| 021 PEP disabled reads NOT_CONFIGURED | Covered | U coverage, C licensed |
| 022 PEP hit routes to EDD | Covered | C licensed |
| 023 former PEP, dates and relationship | Covered for stored hits (rules applied in `licensed.hit_row`, CR-121); the reviewer view does not yet word the codes | `candidate_detail.pep_view`: former is a fact about dates, never lowers risk without a jurisdiction lookback, relationship and named PEP required; U candidate detail (CR-118) |
| 024 media disabled reads NOT_CONFIGURED | Covered | U coverage |
| 025 same-name article not auto-linked | Covered for stored hits (a name-only or contradicted article is never STRONG, CR-121) | `candidate_detail.link_article`: two independent attributes agree and none contradicts, or it is not linked; U candidate detail |
| 026 retraction triggers a case update | Covered | a signed provider event reopens the cases that relied on the article, by the system, with its own reason; C article update |
| 027 unlicensed source rejected | Covered by design | only a registered, approved provider can fill these classes; there is no scraping path |
| 028 no model decides | Covered by absence | no model is in the decision path |
| 029 to 033 ownership and control | Covered (rules, roll-up and a tenant route) | U ownership, U/C ownership roll-up, C EDD API |
| 034 representative screened as lawful | Partly | a representative is a subject type; the market's rule on when is policy |
| 035 tenant isolation | Covered | C requests, C cases API |
| 036 no screening without a legal basis | Covered | U eligibility, C requests |
| 037 forged callback rejected | Covered | C provider events |
| 038 replay changes nothing and bills nothing | Covered | C provider events, C licensed |
| 039 maker and checker server side | Covered | U disposition, C cases API |
| 040 restricted data never in applicant responses or logs | Covered (responses), not run (logs) | a log scan has not been run |
| 041 no cross-region failover | Covered | U provider controls, C licensed |
| 042 rights requests and legal hold | Covered (rights and hold via the privacy module) | C screening privacy; the result is withheld from the export on purpose |
| 043 export redacted and restricted | Covered | U, C exports |
| 044 keyboard and screen reader checks | Not done | |
| 045 provider outage reads unavailable | Covered | C licensed |
| 046 contract disallows feed or market | Covered | U provider controls, C licensed |
| 047 budget stops paid calls before charge | Covered | U, C licensed |
| 048 monitoring dedupe and no storms | Covered (rules) | U watch; the pass opens one run per watch per cadence |
| 049 stale revision refused | Covered | U disposition, C cases API |
| 050 delta reopens prior cases | Covered | as 019 |
| 051 backlog visible under load | Not done | the storm limit is a rule; no load run |
| 052 generic admin cannot approve regulatory action | Covered | U disposition, C cases API |
| 053 release gate fails on an open P0 | Covered | an open P0 defect makes the scope not GO and the customer API and watches refuse; C gates API |

## What only people can close

Legal and AML sign-off on each market and activity; the signed applicability matrix of sanctions regimes; the official download addresses; a contract, a price schedule and Finance authorisation for each provider; independent match-quality evaluation; reviewer training and the PEP no-stigma runbook; G0 to G3 approvals; the DPIA and the retention schedule.
