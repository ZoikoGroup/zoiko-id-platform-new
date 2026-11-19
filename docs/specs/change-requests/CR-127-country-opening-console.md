# CR-127: open or close a feature for a country from the Operator Console

Source: ZID-EDR-001 section 4 (go/no-go per country), CR-048 (person-first sign-up), CR-035 (operator direct mode). Status: engineering built 19 Nov 2026. Not run with a real set of approvals.

## Gap

A country opened a feature (sign-up, identity checks, organization claims, credentials, enterprise use) only by editing `backend/config/country-capabilities/country-capabilities.yaml` (six approvals and a date) and `country-routing.yaml` (a home cell), then restarting the cell. In a deployed environment `SANDBOX` never counts, so no country was open and the people who hold the approvals had no screen to record them.

## What was built

- **Table** `verify.country_opening` (migrations `verification_0034`, `verification_0035`): append-only, one row per decision, the newest version of a (country, capability) is in force. Each of the five regulated capabilities (REGISTRATION, INDIVIDUAL_VERIFICATION, BUSINESS_VERIFICATION, CREDENTIAL_ISSUANCE, ENTERPRISE_USE) is its own switch; only sign-up needs a home cell. An `OPEN` row must carry an effective date, at least one home cell and all six approvals (`legal_assessment`, `supplier_coverage`, `privacy_and_data`, `security_and_resilience`, `local_support`, `signoff`), each with its reference; the database refuses an incomplete one. Platform configuration, no row-level security.
- **Rule** (`verification/domain/countries.py`: `check_opening`, `apply_openings`): an opening makes REGISTRATION `ENABLED` for that country with its approvals and date, a closing makes it `DISABLED` (even where the file says `SANDBOX`). Nothing else about any country changes, and a country the file does not list is ignored.
- **Registry** (`verification/infrastructure/country_registry.py`): the file registry with each country's newest recorded decision applied. The decisions are read into the process at most every 20 seconds on a sign-up request and at once after a change here. If the database cannot be read the last known decisions stay, so a country never seen stays shut.
- **Routing** (`onboarding/application/registration.py`): the home cells of an opened country replace that country's row in `country-routing.yaml`.
- **Operator API** (`operator_access/api/country_openings.py`): `GET /v1/regulatory/country-openings` and `PUT /v1/regulatory/country-openings/{country}` (`action` OPEN or CLOSE). New operator capability `regulatory.country_opening.record` (two-person in governed mode, held directly in direct mode). One transaction holds the capability check, the append-only record and the protected audit record `regulatory.country_opening.recorded` (refusals: `regulatory.country_opening.refused`).
- **Console** ("Country features" under Regulatory): a row per country with an Enable or Disable button for each of the five features, and a count of what still needs approving. Enabling a feature opens the dialog below: pick a country, pick its home cell from the cells this deployment knows, set the date, then drag each of the six approvals from "To confirm" into "Confirmed" (every card also has a Confirm / Undo button, so the board works without a mouse), and open it; or close it. Each approval names its owner. A reference (ticket or document number) can be added to a card but is optional: when none is typed the stored reference reads "Confirmed in the Operator Console on <date>", so the record never carries an invented document number.

## Rules

- The six approvals are the operator's **record** of decisions the owners made elsewhere (`docs/signoff/country-capability-owner-pack.md`). This change gives them a screen; it does not make the decisions. Dragging an approval into Confirmed is the operator's own statement that it exists, and the audit record names who made it; a real reference on the card is what lets anyone find the decision itself. In direct mode one operator confirms all six.
- Each feature opens on its own. Organization claims (BUSINESS_VERIFICATION) stay refused with `COUNTRY_CAPABILITY_UNAVAILABLE` until that switch is open, even when sign-up is. Providers, policy packs, market releases, entitlements and privacy overlays keep their own screens and gates; the regulatory checks that follow the registry run in shadow by default (logged, not refusing) until their owners switch them to enforce, and Enforce readiness shows what each would refuse.
- A country must already be listed in the registry file. Adding a new country is still a file change.
- Closing is a new version, never an edit or a delete.

## Not built

- Drag and drop of whole countries between Closed and Open columns (drag and drop is used for the six approvals inside the open dialog).
- Separate people recording separate approvals (governed mode would need a second operator to approve the capability grant; the six references are entered by one person).

Tests: `tests/unit/test_country_opening.py` (the rules).
