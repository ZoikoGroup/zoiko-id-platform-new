# CR-135: the certificate reader works for any country's layout

Source: owner request, 20 Nov 2026 ("not only GB, for all regions"). Builds on CR-131 and CR-132. Status: built 20 Nov 2026. Not run on real certificates from other countries.

## What was built

- `certificate_read.compare` first looks for the CLAIMED registration number and the CLAIMED company name anywhere in the OCR text. The number is searched as its characters in order, with only spaces, dots, dashes or slashes between them and not inside a longer number; the name as a run of words as long as the name (one more or fewer allowed) whose similarity reaches the usual name match (0.85). When both are present the reading is CONSISTENT, whatever the country, language or layout. No label ("Company Number", "Handelsregister", "SIREN", "CIN") has to be recognised.
- When they are not both present, the UK-label reading of CR-131 applies as before, so another company's UK certificate still reads CONTRADICTS and a poor reading is still NOT_ENOUGH. A claimed number under four characters is never searched for.
- The OCR service returns up to 8000 characters of page text (was 2000), so a dense extract still carries the name and number.

## Limits

- The OCR service reads English only in this flow. Latin-script certificates in other languages usually still read; scripts such as Arabic, Chinese and Cyrillic do not.
- The country must still be open for company verification (CR-127); this change does not touch that gate.
- It proves only that the claimed words are on the picture, as CR-131 and CR-132 say.

## Setup page (CR-134 follow-up)

Organization setup (readiness) now needs only "Legal entity" and "Your identity" to allow activation; the authority and screening items were removed from `readiness.py` as they were from the claim.

Tests: `tests/unit/test_certificate_read.py`, `tests/unit/test_readiness.py`, `tests/component/test_readiness_pg.py`.
