# DPIA (draft): credential trust checks

**Status: DRAFT for the Data Protection Officer. Not approved. Nothing here is legal advice, and no lawful basis, retention period or
transfer mechanism below is decided.** Items marked **DPO** are decisions the engineering team cannot make.
Scope: ZID-GAP-001 v2.0 GID-037 to 065, CR-069. American English. Prepared from the code as built on 2026-11-11, not from the design documents.

## 1. What is processed

A relying party asks whether a person's academic award, professional license, certificate, continuing education, employment record or
insurance is real and current, and may ask whether a regulator holds an adverse finding. Zoiko iD asks a source, records the outcome,
and can turn outcomes into an eligibility or engagement decision for one place and one service.

| Table (single cell database) | What it holds about a person | Sensitivity |
|---|---|---|
| `cred.claim_check` | The subject's pseudonymous id; claim type; decision; evidence tier; jurisdiction; source id and reference; a SHA-256 digest of what was claimed; the assertion (decision facts, license scope and restrictions, insurance region, expiry and exclusions, issuer, explanation, reason text; for employment only fields the employer permits; for a regulator check the outcome) | Personal data; a regulator outcome may be special-category or criminal-offence-adjacent (**DPO**) |
| `cred.claim_monitor` | When a check expires or is next due, its last status and last source sequence | Personal data |
| `cred.consent_grant` | Who a person allowed to see which fields, for what purpose, until when, and withdrawal | Personal data |
| `cred.eligibility_decision` | Outcome, reason codes, restrictions, policy id and version, which checks were read | Personal data; an automated decision (section 6) |
| `cred.claim_appeal` | The person's reason (free text up to 2,000 characters), a correction, the resolution | Free text can contain anything |
| `cred.outreach_request` | The issuer asked, the claim fields asked about (**dropped when the outreach concludes**), state, outcome, reviewer votes | Personal data while open |

**Not stored:** documents, dates of birth, names, addresses, the values a person claimed (except in an open outreach, and the
employer-permitted employment fields), tokens or keys. Events carry ids and decisions only. The public verifier page and API show only the
claim type, decision, tier, status and check date, under a grant the holder shared.

## 2. Purposes and lawful basis (**DPO**)

Verify a claim a person or a relying party presents; decide eligibility for a named service; share a result with a recipient the person
chose; handle appeals. The lawful basis for each purpose, per country pack, is not decided. Sensitive-category checks (a regulator's
adverse findings) are refused unless a consent or legal basis is recorded, an authorized user acts, and a different reviewer is assigned.

## 3. Data flows and residency

Person or relying party, then Zoiko iD (cell database), then a source (registrar, regulator, employer, insurer or an aggregator), then
back. Before any source is asked, the system checks the source contract (permitted purpose, regions, lawful transfer mechanism, validity)
and refuses otherwise. **Today there is no real source: every source is a simulator, so no personal data leaves the platform.** Real
residency and transfer facts must be entered per source and approved (**DPO**, Legal).

## 4. Retention (**DPO**)

No retention period is defined for any of these tables; the privacy data map registers them with no policy code, so the privacy
module's default applies (delete on request unless a legal hold applies). Proposed for the DPO to decide: a fixed period after a check is
superseded; a shorter period for regulator outcomes; deletion of appeal free text with its case. Backups follow the privacy module's
backup erasure window, which is also undecided.

## 5. Rights

| Right | How it works now | Open |
|---|---|---|
| Access | The privacy data map locates every row and exports a readable summary (what was checked, decision, dates, reasons), never a hash or key | none |
| Erasure | `cred.erase_subject` (migration `credential_0008`) removes everything about one subject from all six tables, for the caller's own tenant only, idempotent, after the privacy module's hold and retention checks. Ordinary deletes and edits stay refused by the immutability guards | The immutable design means erasure removes a row rather than editing it; audit evidence of the erasure is kept by the privacy module, not here |
| Rectification | A person appeals; a reviewer other than the person resolves it; an upheld appeal leads to a re-check. A fact from a source is corrected at the source | Credential kinds are not yet registered as rectification sources in the privacy module |
| Withdrawal of consent | A withdrawn grant, and every grant derived from it, discloses nothing from that moment (tested) | none |
| Objection and human review | Every adverse or uncertain result has a reason in plain words and an appeal path | A person-facing appeal screen does not exist; the routes do |

## 6. Automated decisions and fairness

Eligibility and engagement decisions are computed by rules. Safeguards built and tested: an unconfirmed, ambiguous or unavailable source
is never a failure (it is REVIEW, SOURCE_UNAVAILABLE, UNVERIFIABLE or INDETERMINATE); a name-only match is never an adverse finding; an
award recorded as attended is not shown as a degree; no decision function accepts a risk, fraud, device or network signal (a test fails if
one is added); every reason has plain-language text; a person decides anything ambiguous. **Not built or measured:** no fairness or
cohort evaluation of name matching (transliteration and former names are handled by rules, but no accuracy data exists), no WCAG audit,
no translations. These are required before any real launch (**DPO**, QA).

## 7. Risks and mitigations

| Risk | Mitigation built | Residual / decision |
|---|---|---|
| Wrong person matched, adverse result | Ambiguous means review; a regulator finding needs a matched, source-confirmed, final, un-appealed record | Name-matching accuracy unmeasured |
| Over-disclosure to a recipient | Per-recipient, per-purpose, per-consumer grants; field allow-list; never a document or birth date; derived grants only narrow | none known |
| Link leakage of a public verifier link | Capability is a grant in force, one answer for any failure, page is not indexed and shows no identity | Add a no-referrer policy header; links are bearer capabilities (**DPO**: acceptable?) |
| The claim digest is guessable | A SHA-256 of structured claim fields could be confirmed by guessing (low entropy) | Decide whether to key it per tenant (**DPO**, Security) |
| Free-text appeal reasons collect extra data | Length limit; erased with the subject | Consider a structured reason list |
| Over-retention | None defined | **DPO** |
| Sensitive regulator outcomes | Contract, basis, authorized user, reviewer, R3 action; kept only as an outcome code | Legal basis per country (**DPO**, Legal) |
| Cross-border transfer | Source contracts name regions and lawful transfer; a mismatch blocks the query | Real contracts do not exist yet |

## 8. Sign-off

Not signed. Required: DPO, Security and Privacy Officer, Global Regulatory Counsel, Zoikorum Product Owner (Document 01 appendix B). The
independent review of Documents 04 and 05 is a separate human process this draft does not replace.
