# CR-059: One way in (sign-up), and a verified claim establishes the organization (ZID-UX-WF-013A, 13B, 13C)

Status: open (built). Follows the second-wireframe documents: an account is made by person-first sign-up only; the organization exists only when its claim is VERIFIED.

## What changes
- **The company-first `/register` page is removed.** Sign-in and the privacy page point to `/sign-up`. The platform's old company-number endpoint remains for the platform's own use; no screen calls it.
- **Company verification is the claim flow for every country** (`/admin/organization`): step 1 the applicant's identity check, step 2 "Find my organization" (search the official register, authority, status). The old UK
  company-number form is gone. The Overview card for a pending owner reads "Verify your organization".
- **A VERIFIED claim establishes the organization** (`POST /v1/org-claims/{id}/establish`, `onboarding/application/organization.establish_from_verified_claim`, `organization/application/registry.establish_claim_root`).
  Only when the claim's outcome is VERIFIED (all four checks complete, each by its own service) does it: read the official source again (an unreadable or inactive source changes nothing), create the organization and
  its authority root, rename the workspace to the register's legal name (never the applicant's typed name), seed the baseline release and the claimant's tenant-administration grant, activate the pending OWNER and
  move the workspace to setup. A repeat is the same answer; another person's role, an unverified claim and a source outage are each refused with their own code (`TENANT_OWNER_UNAUTHORIZED`, `CLAIM_NOT_VERIFIED`,
  `SOURCE_UNAVAILABLE`). The claim status screen offers "Create the organization", then "Continue to organization setup".
- **The sidebar shows Billing to a person the Owner gave billing access** (Billing Admin, Billing Viewer, Invoice reader), who is not an administrator. Nothing else administrative is shown to them.
- **Sign-up no longer fails hydration:** the first render lists country codes in a fixed order; names (which the browser and the server spell differently) appear after hydration.
- **ZID-EM-074** (High-risk action approval requested, from the Communication Estate spec) is routed from `governance.approval.case_created` to the Owner and Security role contacts. It names the change by its public
  action label and a short reference, never its payload, reason or requester, and says to decide in the approval centre. It sends nothing until an operator enables the template.
- The step-up confirmation dialog has its component test (focus held, Escape cancels, named buttons, a timer, no structural WCAG violation); EDR09-T12 is linked.
- The browser end-to-end helper `registerCompany` now signs a person up through the real form and mail link (a personal workspace with a pending owner). Specs that need an administrator's pages must first verify a claim.

## Tests
`test_org_claim_establish_pg.py` (the establishment, its repeat, and each refusal), `runtime-step-up.test.tsx`, `nav-access.test.ts`, and the coverage list in `test_email_pg.py`.

## Still open, and why
Counsel-approved consent and biometric notices, supplier contracts and sandbox runs, and the owners' rulings (see the owner packs). The establishment has been tested against stand-in registers; a live Companies House run is the
owners' to schedule.
