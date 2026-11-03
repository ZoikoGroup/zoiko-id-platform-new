# Billing permissions and delegation: what only the owners can close (ZID-COM-CL-002, ZID-UX-COM-010; slice L-14a, CR-050)

1. **Founder approval** of Closure 02 and Wireframe 10 and their acceptance tests.
2. **Billing Admin as role 11 or as a grant.** Engineering built it as a bundle of billing permissions the Owner gives (the five roles stay as they are). If the
   owners want a true eleventh role, that is a change to the role matrix and the ruling on conflict 1.
3. **Does Billing Admin read the billing activity record?** The record lists `billing.audit.read` but its matrix does not say who holds it. Engineering gave it to
   the Owner and Billing Admin; Billing Viewer and Invoice reader do not have it.
4. **What a Billing Viewer sees.** Built as overview, invoices and downloads. Whether usage and budgets are part of it is open (they are not gated today).
5. **The enforcement switch** (`backend/config/roles/role-policy.yaml`, `billing_access_enforcement`). ENFORCED is the default and the record's rule. REPORT_ONLY
   lets administrators through while recording each refusal; say when it may be used and for how long.
6. **Grant lifetime.** No maximum is set; an Owner may give an open-ended grant. Decide whether a maximum or a review reminder applies.
7. **Procurement Approver** (an individually appointed, capped delegation) is not built; it needs the signed commitment thresholds of Closure 04.
8. **Provider-hosted portal sessions** and the Stripe customer portal are not built; an invoice-only user never gets one.
