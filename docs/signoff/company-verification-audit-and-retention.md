# Company verification: audit event schema and data retention (DRAFT for sign-off)

**Status:** DRAFT written by engineering from the code as it is today. The schema section describes what is recorded. The retention section lists what is stored and what the system does today; **it does not choose any retention period**. Privacy and Legal choose the periods (WF13B-AT-14).

## 1. Audit records and events

Every row below is an E3 audit record in the evidence ledger. The "event" column says whether the same fact is also published on the event bus (declared in `contracts/asyncapi/proposed-events.yaml`). No row carries a company's personal data beyond identifiers, codes and hashes; none carries document content.

All audit records carry: tenant, event type, actor type (HUMAN or SERVICE), actor principal, action, target type and reference, outcome, reason code (when refused), time, source service and source event id.

| Event type | Event on the bus | Target | Details recorded |
|---|---|---|---|
| `organization.kyb.decided` | yes | `kyb_case` | decision status, reasons, evidence hash, policy version, jurisdiction, version |
| `organization.kyb.verification_lost` | yes | `kyb_case` | case id, previous status, new status, reasons, jurisdiction, version |
| `organization.kyb.proof_added` | yes | `kyb_case` | proof id, kind, media type, size, content hash, scan status |
| `organization.kyb.proof_rejected` | no (audit only) | `kyb_case` | reason code, size, content hash, kind (never the content or the name) |
| `organization.kyb.proof_read` | no (audit only) | `kyb_case` | proof id, content hash; the actor is the reviewer |
| `verification.screening.decided` | yes | screening case | decision status, reason codes, evidence hash, policy version, country, alert count (never names or list text) |

Also recorded by the surrounding services: the evidence-vault object storage event for each stored document (`evidence.object.stored`), vendor cost records for each supplier lookup, and the approval and role records for the people involved.

**Immutability:** company decisions are append-only (`org.kyb_evidence` refuses update and delete); proof records are append-only (`org.kyb_proof`); the audit ledger is append-only. A reviewer's change of mind is a new row.

## 2. What is stored, and what the system does with it today

| Data | Where | Today |
|---|---|---|
| The claim (company, number, the name the applicant gave) | `org.kyb_case` | Kept; not disposed of by any job |
| Each decision with sources, hash, signature, reasons, expiry | `org.kyb_evidence` | Kept; append-only; not disposed of |
| Proof document metadata | `org.kyb_proof` | Kept; append-only |
| Proof document itself | Evidence vault, encrypted, class `kyb_proof`, data class D3 | **Kept until a period is set.** `retention_days` in `config/kyb/proof-policy.yaml` is unset; when set, the vault disposes of a document at that expiry |
| Applicant's file name | Never stored as given; only a short plain label | n/a |
| Screening decisions and alerts | Screening tables | Per the screening policy; list text is never shown to an applicant |
| Audit records | Evidence ledger | Per the evidence retention class (E3) |

## 3. Decisions the owners must record

| Decision | Owner | Recorded |
|---|---|---|
| Retention period for proof documents (and what happens to the metadata row after the document is disposed of) | Privacy / Legal | ______ |
| Retention of company decisions and evidence rows, and of claims that never verified | Privacy / Legal | ______ |
| Whether an applicant can ask for a proof to be erased, and how that meets the need to keep the audit trail | Privacy / Legal | ______ |
| The audit retention class for the events above | Security / Legal | ______ |
| Who may read audit records of reviewer reads, and for how long | Security | ______ |

## 4. Sign-off

| Decision | Owner | Reference | Date |
|---|---|---|---|
| This schema is accepted as the audit event schema | Security / Trust Operations | ______ | ______ |
| The retention schedule is accepted | Privacy / Legal | ______ | ______ |
