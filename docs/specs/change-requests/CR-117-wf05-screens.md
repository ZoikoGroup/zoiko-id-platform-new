# CR-117: WF-05 screens: verification checks console and check method lifecycle (ZID-DTV-WF-05 A01-A10, A12)

Status: open (Step 6 of the WF-05 build).

## What changes
- **Web console, Admin → Verification checks** (`/admin/checks`, `features/admin/checks/checks-page.tsx`): open a case (A01: reference, country,
  purpose, the checks asked for; "This step is unavailable" when none is cleared), the case list, and per case: each result on its own with what it
  proves and does not prove and no overall result (A09); email or phone with a one-time code (A02-A04); address, proof of address documents with the
  maker's review and a second person's check (A05-A07, A10); a code by post (A08); close the case.
- **Operator console, Regulatory → Check methods** (`/regulatory/check-methods`, A12): open proposals (approve or reject; never one's own) and scopes
  (propose the next step, pause or step down).
- **Backend**: `GET /v1/verification-cases` lists the tenant's latest 50 cases. **API client**: `checks` (cell) and the check method calls (operator),
  types in `packages/api-client/src/checks.ts`.

## Not yet
- The customer policy console (A11) and fraud metrics, PII inventory and provider health on the operator side (A12): they need the policy store and
  metrics that do not exist yet.
- Device checks have no screen: observations come from the tenant's own app.
