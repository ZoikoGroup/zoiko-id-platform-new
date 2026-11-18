# CR-115: WF-05 address claims, proof of address documents and two-person review (ZID-DTV-WF-05 DTV-17, §02, §06, A05-A07, A10)

Status: open (Step 4 of the WF-05 build). No address adapter ships, so no case is cleared for an address method until the owners act.

## What changes
- **Address claim** (`PUT /v1/verification-cases/{id}/address`, `verify.check_address`, migration `verification_0024`): the address as typed,
  revisioned. With ADDRESS_LOOKUP cleared, the built-in format check records `ADDRESS_FORMAT_VALID`: PASS only where the market's postcode shape is
  known and right, REVIEW for a bad shape, INDETERMINATE where no shape is known (never a refusal; rural and no-postcode markets are not blocked).
- **Proof of address** (`POST .../address-documents`, `verify.check_address_document`): the evidence upload reference, the document type (refused
  unless the market's policy accepts it, A06), what was read from it (kept apart from the file), and the rules' assessment (`address.assess_proof`).
  The applicant's name is compared and never stored.
- **Review** (`POST .../address-documents/{doc}:review` and `:check`, scope `verification:review`, `verify.check_address_review`, domain
  `address_review.py`): the maker's CONFIRMED records `ADDRESS_DOCUMENT_REVIEWED` PASS and waits for a checker; a different person's agreement records
  `ADDRESS_ASSOCIATION_CONFIRMED` PASS (§02 "acceptable document evidence"; `ADDRESS_DOCUMENT` can now produce it, only through this path). A
  document the rules found DOES_NOT_SUPPORT, or a market with no policy, cannot be confirmed. Findings and reasons never say fraud. The table refuses a
  checker who is the maker and any edit of a review step.

## Not yet
- Address lookup, autocomplete and location providers (libpostal, OS UPRN, OSM), licensed data sources and the postal code (DEC-04, DEC-05).
- The internal operator workbench (A10 with JIT access): the review runs on the tenant plane with `verification:review` until it exists.
- Applicant and reviewer screens (A05-A07, A10).

## Evidence
`tests/unit/test_address_review.py`, `tests/component/test_address_checks_pg.py`.
