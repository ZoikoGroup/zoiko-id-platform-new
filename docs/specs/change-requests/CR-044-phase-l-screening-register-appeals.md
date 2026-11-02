# CR-044: Screening reads the official register, keeps person records in region, and gives a fair second look

**Slice:** L-4 (ZID-ED-04). **Status:** proposed with the slice; the owners confirm the open items at the end.

## 1. What changes

- **Directors and persons with significant control (PSC) from the register.** The Companies House adapter reads a company's officers and
  persons with significant control (`register_people`), in natural name order, with who has ceased; a list the register says is longer than
  the page read is UNAVAILABLE, never a partial answer. The read sends only the company number. A country with no source is NOT_APPLICABLE.
- **Reconciliation.** Everyone the register lists is screened against the lists as well as everyone the customer declared. A current PSC the
  customer left out is `REGISTER_PSC_UNDECLARED` and makes the case GATHERING with an explicit task (a count, never a name); a declared
  director who is not a current officer, a protected entry the register does not name, and a register that could not be read are each a
  REVIEW. An alert always goes to a person before any task.
- **Declare again.** `PUT /v1/org-claims/{id}/screening` while a case is GATHERING opens the case's next screening; the earlier declaration
  and its evidence stay (nothing is rewritten).
- **Data region.** Restricted person records (declared parties, register people, appeal statements) are stored only where the organization's
  residency allows: the check runs before a single name is stored, a refusal is audited (`verification.screening.refused`, a code only) and
  stores nothing. An organization with no recorded residency has no region to hold them in and is refused.
- **Appeal and correction.** `POST /v1/org-claims/{id}/appeal` (`APPEAL` after a rejection, `CORRECTION` while a case is waiting, in review or
  rejected) puts the case back to REVIEW_REQUIRED with the customer's statement. The statement is a restricted record (migration
  `screening_0002`, append-only); only its hash reaches evidence, events and audit. One request is open at a time, and an appeal is decided by
  someone who did not decide the case before (`REVIEWER_CONFLICT`). The reviewer's detail shows the statement, the alerts, the register and
  what the customer declared.
- **Monitoring triggers.** A rescreen can now be asked for by a reason: the register changed (`register_watch` worker step reads every live
  case's register again; a differing hash records a new snapshot and asks), a privileged role was granted or an owner added (the approval
  executors ask), an organization was activated. The next `screening` pass answers and the new evidence names the reason (`triggers`).
  A register that cannot be read changes nothing.
- **Replay.** `GET /v1/reviews/screening-cases/{id}/replay?version=n` (read_restricted) decides again from exactly what that version used (the
  lists by content hash, the parties, the register snapshot, the policy and the clock) and compares with the signed evidence. A reviewer's
  ruling is not replayed. A decision made while a list was stale cannot be reproduced exactly (a list's fetch time moves each time it is
  fetched) and reports `matches: false`.
- **Screening is a security control.** Nothing in the screening module or its routes reads a plan or an entitlement (tests pin it).

## 2. Data

| Table | Scope | Purpose |
|---|---|---|
| `verify.screening_register_snapshot`, `verify.screening_register_person` | tenant (RLS), append-only | What the register said when a case was screened, as numbered snapshots; restricted records |
| `verify.screening_trigger` | tenant (RLS) | A reason to screen again; `handled_version` is the evidence version that answered it |
| `verify.screening_appeal` | tenant (RLS), append-only | The customer's statement (restricted), its hash and the evidence version it produced |

## 3. Not decided here (owners)

- The verified download addresses and an EU account for the lists (until then every decision is UNAVAILABLE), the UN and EU element names
  against a real file, Legal's country packs beyond GB and U.S., thresholds and validity, the compliance reviewers, the fair-review
  procedure and the sign-off (EDR04-07).
- PEP and adverse media: no licensed source exists, so there is no data to screen; the review rule (never reject on PEP status alone) applies
  once a source does.
- Directors from registers other than Companies House, and a register's own protected entries, are for the registry owners.
- Whether a rescreen on privilege escalation should also cover roles granted outside the approval cases.
