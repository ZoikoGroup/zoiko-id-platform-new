# CR-114: WF-05 contact claims, one-time challenges, send limits and the confirmed change of a security email (ZID-DTV-WF-05 DTV-20, DTV-21, §06, §22, A02-A04; QA-001 to QA-016)

Status: open (Step 3 of the WF-05 build). No challenge sender and no challenge adapter ship, and the shipped limits send no SMS or voice anywhere, so
nothing is sent to an applicant until the owners act.

## What changes
- **Contacts** (`verification/domain/contact_challenges.py`): phones parsed with libphonenumber (`phonenumbers`, new dependency) to E.164; a possible
  but unusual number proceeds marked `unusual_number` (QA-002). Email keeps its local part exactly as typed; only the domain is lower-cased and
  IDNA-encoded (QA-010). Values are encrypted in the evidence vault; the table keeps a tenant-keyed hash and the masked form (`verify.check_contact`,
  migration `verification_0023`). A changed contact is a new revision and revokes every open challenge of the old one (QA-003, QA-014).
- **Challenges** (`verify.check_challenge`): a 6-digit code by SMS or voice (A03), a 43-character single-use link secret by email (A04). Only a keyed
  hash is kept. At most 5 minutes, 5 attempts, 3 sends 60 seconds apart; a resend never extends the expiry (QA-005). Bound to the contact revision and
  the asking session (QA-016). Used once (QA-004). The table refuses edits to a final challenge, to its binding and expiry, and lowered counters.
- **Send limits** (`config/verification/contact-challenges.yaml`, SAMPLE): per contact, case and calling code per hour, and a daily cap per tenant and
  channel. SMS and voice go only to listed calling codes: none are listed and both caps are 0 (DEC-06). One code for every limit (QA-009).
- **API**: `POST /v1/verification-cases/{id}/contacts`, `POST /v1/contacts/{id}/challenges` (Idempotency-Key; a replay sends nothing),
  `POST /v1/contacts/{id}/challenges/{challenge_id}:resend`, `POST /v1/contacts/{id}/confirm`. A confirmed challenge records
  `CONTACT_CONTROL_CONFIRMED` (PASS) on the case and nothing else. No answer carries the full value or the code. Delivery goes to
  `app.state.contact_challenge_sender` after commit.
- **Security email change** (`communication/application/contact_change.py`, `platform.email_contact_change`, migration `communication_0002`):
  `PUT /v1/account/contact` now returns 202 and changes nothing until the new address opens the single-use link (ZID-EM-017, route
  `/account/email-change`); the old address gets ZID-EM-018; `POST /v1/account/contact:confirm` applies it once and ZID-EM-019 goes to both. Web: the
  confirm page `/account/email-change` and the contacts screen wording.

## Not yet
- No challenge sender: a case's email needs a catalogue template the owners approve (ZID-EM-009 is worded for sign-in and ZID-EM-001's link goes to
  sign-up), and SMS or voice needs a supplier (DEC-05, DEC-06, DEC-09). Until then `EMAIL_CHALLENGE` and the phone methods have no adapter.
- Carrier and SIM-swap intelligence (DEC-09), and the applicant screens A02-A04 (screens step).

## Evidence
`tests/unit/test_contact_challenges.py`, `tests/component/test_contact_challenges_pg.py`, `tests/component/test_email_pg.py`.
