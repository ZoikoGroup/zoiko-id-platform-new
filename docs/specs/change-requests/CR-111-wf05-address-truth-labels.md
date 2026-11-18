# CR-111: Address truth labels, non-Latin address comparison and a per-market proof policy (ZID-DTV-WF-05 §02, A05, A06; QA-022; DEC-03)

Status: open (domain rules and configuration changed and unit tested; no route, store or screen yet: those are later WF-05 steps). Step 1 of the WF-05
build. It fixes existing code that contradicted Wireframe 05 before anything new is built on top of it.

## Why
Wireframe 05 (Global Address, Contact & Device Verification) keeps six evidence assertions apart (§02) and says none may be silently turned into
"verified". A review of the code against it found four places that did not hold:

1. **Non-Latin addresses were erased before comparison.** `address._fold` decomposed the text and dropped every non-ASCII character. Two different
   Japanese, Cyrillic, Arabic or Devanagari addresses with no postcode both folded to their digits alone, so `compare` said SAME. For example, a Tokyo
   address and a different Osaka address both became `1 1`, and the answer was SAME. A name in those scripts folded to nothing, so `_same_name` said
   DIFFERENT and a document got a false NAME_MISMATCH. WF-05 A05 and QA-022 require non-Latin addresses to be supported.
2. **An address had a single "Verified" label.** `tiers.address_label(verified=True)` returned "Verified" (`tier-policy.yaml`
   `label_when_verified`). §02 separates format, location, document review and association; a location found is not proof that someone lives there.
3. **A workflow could approve on an address check alone.** `Check.ADDRESS` coming back VERIFIED could lead straight to APPROVED.
4. **The proof of address policy was one global list** with a 90-day age for utility bills and bank statements. A06 says "do not impose a universal
   90-day age rule", and DEC-03 asks for a table per jurisdiction.

## What changes
- **Assertions** (`verification/domain/assertions.py`, new, pure): the six §02 assertions (`ADDRESS_FORMAT_VALID`, `ADDRESS_LOCATION_MATCH`,
  `ADDRESS_DOCUMENT_REVIEWED`, `ADDRESS_ASSOCIATION_CONFIRMED`, `CONTACT_CONTROL_CONFIRMED`, `DEVICE_RISK_OBSERVED`), with what each proves and what it
  does not. `strongest_address` chooses which held assertion to show; holding one never implies another.
- **Comparison** (`verification/domain/address.py`): folding keeps every script. Only a mark on a Latin letter is dropped (é is e). A mark in another
  script can change the word (が is not か; राम is not रिम), so it is kept. Full-width digits are the same digits. Two texts in different scripts (a native
  spelling and a transliteration) are never SAME: the answer is PARTIAL, so a person compares them, or DIFFERENT when the postcodes differ. An address
  with nothing comparable left is never SAME. For names, a name in another script is NAME_PARTIAL_MATCH (REVIEW), not a mismatch, and the same letters
  split differently (李 小龍, Annmarie and Ann Marie) are PARTIAL, not SAME.
- **Per-market policy**: `ProofPolicy.markets` maps an ISO country to its accepted document types and ages. A file with the old global `max_age_days`
  is refused. `assess_proof` takes the market (default: the claimed address's country). A market the policy does not name is never SUPPORTS: the answer is
  REVIEW with `NO_POLICY_FOR_MARKET`, and the document's type and age are not judged under another market's rule. `accepted_types(policy, market)` is
  empty for such a market. The shipped SAMPLE names only GB, because its sample document types are British.
- **Tier labels** (`verification/domain/tiers.py`, `config/verification/tier-policy.yaml`): `address_label(entered, held, policy)` shows the label of the
  strongest address assertion actually held. The policy must give one label per address assertion. `label_when_verified`, a missing assertion, or a label
  claiming "verified" (other than "not verified") is refused when the policy loads.
- **Workflows** (`verification/domain/workflow.py`): `Check.ADDRESS` joins the checks that need a person after them before APPROVED. The new problem code
  is `ADDRESS_CHECK_AUTO_APPROVES`, and its wording is in the web console (`features/admin/workflows/logic.ts`). No shipped workflow used ADDRESS.

## What it does not do
- No route, table or screen for address, contact or device evidence: those are WF-05 steps 2 to 6.
- **Re-checking a changed email** (`PUT /v1/account/contact` still replaces the address without a check) is left for step 3, where it is built on the
  one-time challenge engine instead of as a one-off.
- The global email hash with cross-tenant lookup at sign-in (`communication/application/contacts.py`, `registration_account`) and the device cookie set
  before any purpose or notice gate wait for owner decisions DEC-02 and DEC-08.
- The proof of address policy is still a SAMPLE nobody approved.

## Evidence
`backend/app/tests/unit/test_address.py` (non-Latin comparison, marks outside Latin, transliteration, nothing comparable, cross-script names, per-market
policy, unnamed market). The Tokyo and Osaka pair was confirmed to compare SAME on the code before this change.
`backend/app/tests/unit/test_verification_tiers.py` (labels per assertion; a single verified label refused).
`backend/app/tests/unit/test_workflow.py` (`test_an_address_check_never_approves_on_its_own_a_person_looks_after_it`).
Web: `features/admin/workflows` tests.

## Owner decisions still needed
1. DEC-03: the accepted proof of address documents and their ages for each market.
2. Whether a transliterated address or name may ever be matched automatically (today a person always compares them).

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision |
|---|---|---|---|
| Product owner | | | |
| Compliance officer | | | |
| Engineering owner | | | |
