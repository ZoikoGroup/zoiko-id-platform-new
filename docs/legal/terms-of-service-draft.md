# Zoiko iD Terms of Service: DRAFT FOR COUNSEL

**Status: engineering draft. Not legal advice and not approved. Not published.** Counsel owns the wording, the contracting entity, the governing law and every figure marked `[COUNSEL]`. Engineering wrote this only so that what the terms say about the product matches what the platform does. Publishing is a change to `frontend/apps/web/src/lib/legal.ts` (`TERMS.published = true`, a `version`) and to the terms page, made after counsel signs; the sign-up page then shows the link by itself.

How the draft was built: from the product as it runs (organizations, verification, approvals, agents, billing, evidence) and from the sign-up wireframe (13A), which requires a Terms link beside the Privacy Notice. Where a statement below depends on a rule the platform enforces, the rule is named so counsel can check it.

---

## 1. Who we are and who you are
1.1 "Zoiko" means `[COUNSEL: contracting entity, registered number, address]`. "You" means the person creating an account, and, where you act for an organization, that organization too.
1.2 You confirm you are old enough to enter a contract in your country `[COUNSEL: minimum age]`, and, if you act for an organization, that you are authorized to bind it.

## 2. The service
2.1 Zoiko iD provides identity, verification, authorization and audit services: personal accounts, organization accounts, verified claims about people and organizations, approvals, delegated authority, credentials, and the records that show who decided what.
2.2 Features depend on your plan. A feature your plan does not include is not available, and we may change plan contents with notice under section 10.
2.3 Test (sandbox) identities are for testing only and carry no verified status.

## 3. Your account
3.1 Give accurate information and keep it current. You must prove an email address before it is used for anything that matters; we store only a one-way fingerprint after it is proved.
3.2 Keep your sign-in methods safe. Tell us at once if you think someone else can use your account. We may end sessions or restrict an account when our security checks say it is at risk.
3.3 Some actions need a second sign-in step or a second person's approval. These are controls that protect you; you may not try to bypass them.
3.4 Organization accounts are controlled by the people recorded as their owners. Owners decide who may act and may remove a member. Changes to ownership follow the approval rules shown in the console.

## 4. Verification
4.1 We may ask you to verify your identity or your organization's identity, using providers we choose. Verification results are our assessment at a point in time and may be refreshed, restricted or withdrawn.
4.2 We do not state that a verification level equals any external standard (for example NIST IAL) unless we say so in writing `[COUNSEL]`.
4.3 You must not submit documents or information that are false, belong to someone else, or that you have no right to give us.

## 5. Acceptable use
You must not: attack, probe or overload the service; use it to deceive anyone about who they are dealing with; share credentials or tokens; scrape other people's data; reverse engineer it except as the law allows; or use an AI agent or automation under our identity features in a way that exceeds the authority you gave it. Agents act only within the authority an organization grants; the organization is responsible for what its agents are authorized to do.

## 6. Your data
6.1 How we handle personal data is in the Privacy Notice, which forms part of these terms for that purpose.
6.2 You keep your rights over your data. Where the law lets you access, correct, export or erase it, use the privacy requests in your account. Some records (audit and evidence records, legal holds, financial records) are kept for the period the law or the record's purpose requires and cannot be erased before then `[COUNSEL: retention wording per jurisdiction]`.
6.3 Your organization's data is stored in the region chosen for it. We move it only as the residency rules and your agreement allow.

## 7. Fees and billing
7.1 Paid plans, prices, usage charges and taxes are those shown at purchase or in your order `[COUNSEL/FINANCE: no price appears in these terms]`. A plan is paid for before it is activated.
7.2 Invoices are issued in the currency shown. Taxes are added where they apply, using the details you give us. You are responsible for the accuracy of your billing details.
7.3 If a payment fails we tell you, and we may limit paid features until it is resolved. We do not hold full card details.
7.4 Refunds and credits are decided by the process in your order and our refund policy `[FINANCE/COUNSEL]`. A refund is made to the original payment method unless the law requires otherwise.
7.5 You may dispute an invoice or a charge by contacting us before the due date.

## 8. Availability and support
We aim to keep the service available but do not promise it will be uninterrupted. Service levels, if any, are in your order `[COMMERCIAL]`. We announce planned maintenance. Security and legal notices are sent by email to the contacts you set; you must keep them current.

## 9. Our rights and yours in the content
The service, its software and its content belong to Zoiko or its licensors. You receive a limited, non-transferable right to use it for your own purposes while your account is active. You keep ownership of what you submit and give us the right to process it to provide the service, secure it and meet our legal duties.

## 10. Changes to these terms
We may update these terms. We give notice of a material change before it applies `[COUNSEL: notice period]`, and show the version that applies. Using the service after a change takes effect means you accept it, except where the law requires your agreement.

## 11. Ending
11.1 You may close your account at any time; some records remain as section 6.2 says.
11.2 We may suspend or end your access for a serious or repeated breach, for legal reasons, or to protect people or the service. Where we can, we tell you why and how to respond.
11.3 Clauses that by their nature continue (payment due, data retention, liability, disputes) continue after the account ends.

## 12. Liability
`[COUNSEL: warranty disclaimer, exclusions, liability cap, carve-outs for what cannot be limited by law, indemnities]`. Engineering takes no position on these.

## 13. Law and disputes
`[COUNSEL: governing law, venue, consumer-law protections, dispute procedure, class-action wording where relevant]`.

## 14. Contact
`[COUNSEL/OPERATIONS: legal notices address, support contact]`.

---

### Open items for counsel (what engineering cannot decide)
| # | Item | Where it appears |
|---|---|---|
| 1 | Contracting entity, address, registration number | 1.1, 14 |
| 2 | Minimum age and consumer-law carve-outs | 1.2, 13 |
| 3 | Whether any verification level may be described as an external standard | 4.2 |
| 4 | Retention periods per jurisdiction | 6.2 |
| 5 | Pricing, refund, SLA terms, notice period for changes | 7, 8, 10 |
| 6 | Liability, warranty, indemnity, governing law, venue | 12, 13 |
| 7 | Whether enterprise customers sign a separate master agreement that overrides these terms | whole |

### Statements engineering confirmed against the platform
- A paid plan is activated only after payment (CR-062).
- Sensitive actions need step-up or a second approver (approvals service, step-up route guard).
- Only a fingerprint of a proved email is kept after proof (sign-up design, privacy adapter).
- Organization data stays in its home region unless the residency rules allow a transfer.
- Audit and evidence records are retained under their own schedule and are not erased on request before it ends.

### Publishing checklist (after counsel approval)
1. Put the approved text in `frontend/apps/web/src/app/(auth)/legal/terms/page.tsx`.
2. Set `TERMS = { published: true, version: "<date>" }` in `frontend/apps/web/src/lib/legal.ts`.
3. Record acceptance of the terms version with the notice version at sign-up (the cell refuses a sign-up that acknowledged any other version for the privacy notice; extend the same rule if counsel requires acceptance of terms).
4. Add the version to the sign-up owner pack and the acceptance rows.
