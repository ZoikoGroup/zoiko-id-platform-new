# Company verification: reviewer runbook (DRAFT for sign-off)

**Status:** DRAFT written by engineering from how the system behaves today. It is not approved. Trust Operations and Legal own it; WF13B-AT-14 needs their signature before production. Nothing here changes what the system does.

**Who this is for:** a reviewer who rules on a company claim (a person holding the protected action `organization.kyb.review`) and, separately, one who reviews a sanctions or ownership screening case (`verification.screening.review`, `verification.screening.read_restricted`). Holding the OWNER or ADMIN role does not make anyone a reviewer.

## 1. What you are deciding

A company claim has four independent checks: the company and its legal status, the applicant's identity, the applicant's authority to act for it, and the risk and ownership review. You rule only on the **company** check (this runbook) or on a **screening** case (section 5). A ruling never decides the other checks, and a document the applicant uploaded never verifies anything by itself.

## 2. When a company case reaches you

The service puts a case in review (status NEEDS_REVIEW) when the evidence is not a clean match. The reason is on the case:

| Reason shown | What it means | What to look at |
|---|---|---|
| Changed legal name | The register's name differs from the name the applicant gave | Certificate of change of name, register history |
| Inactive or dissolved entity | The register shows the company as not active | The register entry and its date |
| Absent register entry | The register has no such company | Whether the number or country was mistyped; a company that is not in a register is not "denied", only unconfirmed |
| Different entity type | The legal form differs | The certificate and the register's form |
| Sources disagree | Two sources give different answers | Which is the official register; a supplier fact counts only where its coverage is proven |
| Possible different entity | A close name or number matched another company | The registration number and jurisdiction, not the name |

A timeout, an outage or no source coverage is **UNAVAILABLE**, never a failure of the company. Those cases are not yours to rule; they are refreshed when the source returns.

## 3. How to rule

1. Open the case's documents (`GET /v1/reviews/kyb-cases/{id}/proofs`, then each one). Every read is audited with your identity and the time. Read only what the decision needs.
2. Compare what the applicant says, what the register says and what the documents say. A name or similarity alone proves nothing.
3. Choose one outcome:
   - **VERIFIED**: the official source and the documents agree the company is who the applicant says. The verification stands for the validity period (365 days is proposed) and is read again about 30 days before it lapses.
   - **REJECTED**: the evidence shows it is not that company, or the company is not in good standing.
   - **NEEDS_REVIEW**: you need more from the applicant; the case stays open.
4. Write a rationale of at least 10 characters that another reviewer could follow later: what you saw, what it means, what you relied on. The service refuses a ruling without one.
5. Submit. The decision is a new signed evidence row; earlier rows are never changed.

**You cannot rule on a claim you opened.** The person who requested the claim is refused with `REVIEWER_IS_REQUESTER`. If you are the only person who can review it, say so to Trust Operations; do not work around it.

## 4. When a verified company stops being one

The platform reads the sources again before a verification lapses. If a verified company is now dissolved, renamed or a different form, the claim drops out of every view at once (the organization's checklist becomes limited or restricted), one `organization.kyb.verification_lost` event and audit record are written, and the earlier evidence stays. The case returns to review with the new reason. Rule on it as in section 3. If the sources are only temporarily unavailable, a standing verification is **not** removed.

## 5. Screening cases (sanctions, directors, owners)

Applicants never see list content. You see the possible matches with the list and entry; every possible match is held for a person, never an automatic final denial and never a public accusation.

1. Compare the listed person's details with the person on the claim (name, year of birth, country, role). A shared name is not a match.
2. Rule **VERIFIED** (cleared, with the reason a namesake is not the person), **REJECTED**, or leave it held. A written rationale is required.
3. Do not discuss list content with the applicant. Tell them only the outcome and next step the screen shows.

## 6. What you must never do

- Rule on a claim you requested, or on one where you have a conflict. Say so and hand it over.
- Download, copy or forward a proof document outside the case. Reads are audited.
- Treat an upload, a payment, a registry match or a plan as proof of anything.
- Tell an applicant whether another organization has claimed the same company.

## 7. Evidence you leave behind

Every ruling records your identity, the time, the outcome, the rationale, the policy version and the signed evidence hash. Every document you open records `organization.kyb.proof_read` with the document and its hash. These are kept in the evidence ledger (class E3).

## 8. Sign-off

| Decision | Owner | Reference | Date |
|---|---|---|---|
| This runbook is accepted as the reviewer procedure | Trust Operations | ______ | ______ |
| The rationale standard and the conflict rule are accepted | Legal | ______ | ______ |
| The validity period and the refresh lead time | Trust Operations | ______ | ______ |
| Who may hold the reviewer action and how reviewers are trained and rotated | Security / Trust Operations | ______ | ______ |
