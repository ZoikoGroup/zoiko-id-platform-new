# WF12 (Public Trust Center, certification, evidence and claims governance): what exists, what CR-174 slice 1 added, what is left

Written 25 Nov 2026 for the Zoiko iD product owner. ZID-CP-WF12 v2.0 asks for a dual-plane Trust Center: a public projection of approved evidence, and a private claims and evidence governance plane. This is the reconciliation from reading the code, and it records slice 1. It makes no certification, uptime, customer-count or provider-status claim, and nothing here proves a certification exists.

**Headline.** Before this slice the repo had one piece of WF12: the Verify API claims feed (`verify_api/domain/claims.py`, CR-173), which lists what a developer-platform sentence may say and what it may not, and a public page for it at `/docs/verify-api/trust`. There was no claims register, no evidence model, no approval workflow, no publication gate, no security.txt and no availability rule. Slice 1 adds the **rules**, as a pure domain with tests, in a new module `trust_center`. It adds no database, route or screen yet.

## What slice 1 built (`modules/trust_center/domain`)

| Wireframe row | Rule built | Where | Test |
|---|---|---|---|
| 12.09 claim taxonomy and risk | Eight claim classes, each needing at least two distinct approver roles; a sentence that sounds like a certification (SOC 2, ISO 27001, FedRAMP, HIPAA, "compliant", "certified") is classified up whatever the writer chose | `claims.py` | `test_a_role_the_claim_class_does_not_need...`, `test_every_claim_class_needs_at_least_two...` |
| 12.11 immutable approvals | Content hash over exact text, locale, scope, channels, evidence digests and expiry; an approval binds to that hash, so one character, a new revision or a changed evidence digest voids it | `claims.py` | `test_one_character_of_copy...`, `test_a_changed_scope_locale...`, `test_changed_evidence_bytes...`, `test_approvals_do_not_carry...` |
| 12.11 separation of duties | The author cannot approve or refuse; one person cannot cover two roles; a role cannot approve twice; approving evidence other than the evidence named is refused; an approval past its date stops counting | `claims.py` | `test_nobody_approves_their_own_claim`, `test_the_same_person_or_the_same_role...`, `test_an_approval_past_its_date...` |
| 12.11 publish-time checks | Everything is re-checked at publish: hash equals the approved one, claim not expired, evidence on record, verified, current, in scope, not self-referential, supplier evidence not shown as ours, approvals complete | `claims.publish_blockers` | the parametrized stale/unscoped/unverified/self-referential test, `test_a_tampered_bundle...`, `test_a_suppliers_certificate...` |
| 12.11 emergency withdrawal and rollback | One responder, with a reason, withdraws at once and a review is demanded; a withdrawn claim cannot return unchanged; rollback goes only to the latest validly approved, never-withdrawn version | `claims.py` | `test_one_responder_withdraws...`, `test_only_a_responder...`, `test_a_withdrawn_claim_cannot_return...`, `test_rollback_goes_only_to...` |
| 12.01 public projection | Only a published, unexpired claim is projected; no author, approval or reviewer detail is in it; it carries `effective_as_of` | `claims.project` | `test_only_a_published_unexpired_claim_is_projected...` |
| 12.02 and 12.10 certification semantics | SOC 2 is a report, never a certificate; ISO 27001 needs a certificate, not a self-assessment; an expired artifact is EXPIRED whatever its stored state; a supplier's or unconfirmed artifact never supports "Zoiko iD holds"; a subset (other entity, product, region, period, or an unstated region) cannot support a wider claim; FedRAMP needs a current official program listing for that exact offering | `evidence.py` | the seven `test_...artifact...`, `test_a_fedramp_claim...`, `test_a_subset_cannot...` |
| 12.10 figures and endorsements | A figure (6B+, 150+, 50 ms) needs a definition, a reproducible query, a source system, a window and reconciliation; a named customer story needs signed, current permission for exactly the name, logo and results it shows | `evidence.py` | `test_a_figure_like_6b...`, `test_a_named_customer_story...` |
| 12.06 availability | No snapshot, an unvalidated one or a zero denominator is "not yet reported", never 100%; a figure must reproduce from raw telemetry within tolerance; a service target is worded as a target and shown only for a covered tier, never to free or sandbox | `availability.py` | `test_an_empty_history...`, `test_a_published_figure...`, `test_a_service_target...` |
| 12.07 security.txt | RFC 9116 renderer that refuses a file with no contact, a past or over-long expiry, or a non-https URI; a checker that reads back a served file (content type, contact, one valid expiry, a canonical that matches the URL); a placeholder and dead-link detector | `security_txt.py` | `test_security_txt_*`, `test_an_expired_or_contactless...`, `test_the_checker_names...`, `test_placeholder_and_broken_links...` |

Sixty acceptance rows `WF12-QA-01..60` (source CR-174) are in `docs/traceability/acceptance-tests.csv`: **27 IN_PROGRESS** (rule tested, nothing wired to a route or a store), 33 NOT_STARTED.

## Slices 2 to 5 (same day, CR-174): storage, workflow, public surfaces, console

- **Storage** (migrations `trust_center_0001` to `0003`, 15 tables in `platform.tc_*`): evidence, claim revisions, approvals, publications, withdrawals, availability snapshots, advisories and versions, vulnerability reports and timeline, privacy disclosures, restricted-file bytes, room requests, grants and access history. The database itself refuses the author's own approval or publication, an approval for another hash, one approver or role counting twice, publication with fewer than two approvers, a withdrawn revision returning, edits to content, and deletes.
- **Operator API** `/v1/trust-center/*` (staff only): every action is a just-in-time operator capability (`trust.claim.author|review_<role>|publish|withdraw`, `trust.evidence.manage`, `trust.status.*`, `trust.advisory.*`, `trust.privacy.*`, `trust.report.triage`, `trust.room.*`) with conflict rules, a protected audit record, and an outbox event for publish, withdraw and evidence change. A reviewer's role is the capability held, not a request field.
- **Public API** (no sign-in): `/public/trust/v1/claims|certifications|advisories|privacy`, `/public/status/v1/availability`, `/.well-known/security.txt` (served only with a configured contact and an unexpired date), and `POST /public/trust/v1/vulnerability-reports` (reference and target back, never the text; five per hour per source). A claim whose evidence lapses disappears from the public read at once; the worker sweep (`trust_center`) records it.
- **Evidence room** `/trust/v1/*`: signed-in business asks with purpose and accepted terms; staff approve; the token works only for the requester and company, expires, has a download cap, is watermarked, and every attempt is recorded. Files are malware-scanned and digest-checked before storage.
- **Monitoring**: `/v1/trust-center/monitoring` (release gate: zero unsupported live claims, no overdue withdrawal review, no report past first-response target; expiry watch) and `/scans` (claim drift), plus `tools/scripts/trust_claim_scan.py` for a live site and dead links.
- **Screens**: operator console "Trust Center" (Public claims; Trust operations with monitoring, evidence, reports, withdrawals, availability, privacy, document requests) and public pages `/trust`, `/trust/certifications`, `/trust/privacy`, `/trust/status`, `/trust/advisories`, `/trust/security`, with axe checks and keyboard test.
- **Tests**: 60 unit, 2+5+3 PostgreSQL component, 2 scanner, 7 web, 5 console-logic. Rows `WF12-QA-xx`: 53 IN_PROGRESS.

## Still not done, and not buildable here

- **Owner decisions** DEC-01..16: approver roles per claim class, the 72 h review window, acknowledgement targets, evidence-room terms (`ROOM_TERMS_VERSION` is a draft), notice wording. All are placeholders in code.
- **Real content and evidence**: no claim, certificate, subprocessor, figure or customer permission exists in the register. The live homepage review (TRU-01/02) is a Product, Legal and Security task; the scanner is the tool for it.
- **Deployment settings to set**: `ZID_TRUST_SECURITY_CONTACT`, `ZID_TRUST_SECURITY_EXPIRES`, policy URL, `ZID_TRUST_REPORT_SALT` (change the default), a real malware scanner, and the on-call route for `trust.vulnerability.reported`.
- **Open rows**: CDN purge (QA-49), real WF-02 telemetry feeding snapshots (a person records them from raw counts today), statutory notification tracking, RTL and screen-reader review (QA-60), external accessibility audit, localization (TRU-16), board dashboard export. No customer-targeted incident notification yet (QA-33 is the public side only).
- **Known failures not ours**: authority migration table ownership, module dependency cycles, empty `sdks/java/target` folders, WF10's a11y test type error and missing `qrcode` types.
- Run vitest from the upper-case `C:` path on Windows; the lower-case path breaks suite discovery.

## Wiring the guards needed

`trust_center` joins the `security-evidence` plane (`roles.py`) and the evidence capability (`contracts/baseline/capabilities.yaml`), next to `evidence`, `privacy` and `assurance`.

## Not built (the rest of CR-174)

- **No storage.** No tables for `trust_claim`, `evidence_artifact`, `claim_evidence_link`, `claim_approval`, `site_publication`, `security_document_grant`, `privacy_disclosure`, `service_sli_snapshot`, `incident_advisory` or `claims_exception`; no migration; no table ownership entries; no append-only or RLS rules.
- **No API or events.** None of the routes in section 07 (`/public/trust/v1/claims`, `/certifications`, `/trust/v1/evidence-requests`, `/claims`, `/review`, `/publications`, `/withdraw`, `/public/status/v1/availability`, `/.well-known/security.txt`) and no `trust.claim.withdrawn` event. Publication into a CMS or CDN is the company's, and so is the cache purge.
- **No screens.** None of the 12 wireframes: the public Trust Center, the certification catalog, the request room, the security overview, the privacy and subprocessor center, the status page, the disclosure page, advisories, the claims register, the dossier, the approval gate, the monitoring dashboard.
- **Rows that need systems that do not exist here:** the restricted evidence room (NDA, signed grants, watermarking, malware scan: QA-21 to QA-30), incident and statutory notification (QA-33, QA-40, QA-50), subprocessor and transfer facts from WF-09 (QA-34 to QA-39), the live-site claim scanner and sales-asset drift (QA-51 to QA-53), CDN purge (QA-49), the accessibility audit and RTL review (QA-59, QA-60).
- **Authoritative numbers.** No uptime, customer count, volume or latency figure exists here. The rules refuse them without a source; the source systems (Finance, SRE, CRM) are the company's.

## Decisions the owners must make (from section 11) before the rest can be built

- **DEC-02:** the exact claim classes, status labels and prohibited wording. Slice 1 uses the eight classes and the word list in `claims.py` as a starting point, not as an owner decision.
- **DEC-13:** which roles approve which class. `REQUIRED_ROLES` is a proposal; the code needs the owners' table.
- **DEC-01:** the emergency-withdrawal roles and the retrospective review window (72 hours is a placeholder).
- **DEC-04, DEC-05, DEC-07, DEC-08:** which reports, certificates, customer permissions and uptime method are real. Until then the register holds no claim and the Trust Center would show only what it can back.

## The wireframe's own immediate safety gate

The document asks for the live `zoikoid.com` claims (6B+, 150+, 99.99%, SOC 2, ISO 27001, FedRAMP, HIPAA, TechCorp and SecureFlow stories, placeholder links) to be reviewed or withdrawn within five working days. That is a content and evidence task for Product, Legal and Security, not code. This slice supplies the rules the review would use; it does not review anything.

## Next slices, in order

1. Migration `trust_center_0001` with append-only revisions and approvals, RLS, table ownership and the event declarations; the register and approval routes behind a protected action with the hash and separation-of-duties checks in the database too.
2. Public read routes (claims, certifications, availability, `/.well-known/security.txt`) and the public Trust Center page, with the claims feed from CR-173 as its first data source.
3. Internal console: register, dossier, approval gate, emergency withdraw.
4. Evidence request room, privacy and subprocessor center (reads WF-09), advisories, monitoring.
