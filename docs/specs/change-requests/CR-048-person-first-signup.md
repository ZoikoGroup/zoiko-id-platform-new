# CR-048: Person-first sign-up and account creation (ZID-UX-WF-013A), slice L-11

Status: open (built). The wireframe is proposed and awaits founder approval; its API names are illustrative, so this CR records the names used.

## What changes
- **Routes (`/v1/registration/*`, unauthenticated, off unless the cell has a registration tenant):** `GET capabilities?country=`, `POST intents`, `POST resend`, `POST confirm`, `GET status?request_id=`. The record's `POST /v1/authenticators/enroll` is the existing enrollment-ticket passkey ceremony, and its `POST /v1/organizations/claims` is the existing `POST /v1/org-claims` (L-12); both are reused, not duplicated.
- **Order of work (AC-06):** country, journey, email, locale, notice version and a signed nonce are validated, the country is resolved to a home cell from `config/registration/country-routing.yaml`, and only then is anything stored. A country with no home cell is refused honestly (`REGION_UNAVAILABLE`, "Nothing has been stored"), a country held by another cell is `CELL_ROUTING_REQUIRED` (421) with that cell's URL, and nothing ever defaults to GB, UK rules or +44 (AC-07). The notice version must be the current one.
- **The server derives everything.** The body has exactly the form's fields (`extra=forbid`): no tenant, role, assurance level, verification status, home region, phone, address, card or biometric. A sign-up produces an account that is ACTIVE and NOT identity verified, in a personal workspace whose only role is the pending bootstrap Owner (no organizational privilege), whose tenant is never verified (AC-01, AC-02).
- **Email is proof of an inbox only.** Canonicalization is Unicode NFC, ASCII lower-case domain, case-insensitive local part, and nothing else: no +tag stripping, no dot removal. The link is a 256-bit single-use token stored as a hash, valid 30 minutes, found by its hash (so it works on another device); replay, expiry, a different request's link, a stale request and too many wrong guesses all answer one message, "This link is no longer valid. Request a new link." (AC-05).
- **No enumeration (AC-04):** a known address, an unknown one, a flooded one and a held one get the same 202 body and the same public status (`PENDING_VERIFICATION`); only the real inbox is mailed, after the response, from a background task. A resend is generic for everything. The status route never shows the internal hold label (`ON_HOLD`) or a score.
- **Duplicates and races (AC-08):** one idempotency key is one request; a new request for an address ends its earlier open requests; the keyed email hash is the account index, written once, so two confirms racing make one account.
- **Minimal data (AC-11, privacy):** the address is held in clear in the request only until it is proved, then nulled (and nulled by expiry and by the maintenance sweep); afterwards only a keyed hash remains, and the address moves into the person's own contact vault. Events carry ids, country and journey only.
- **Invitations (AC-03):** `POST /v1/tenant/members` accepts an optional `email`. Only its keyed hash is indexed; after the inbox is proved, the address is routed to the live invitation (a fresh single-use enrollment ticket for the invited account, no personal workspace created) and the response names no organization. The role is the one the inviter issued, and a privileged role was never directly grantable.
- **Anti-abuse:** a signed nonce from `capabilities` (30 minutes) is required; a missing or stale one is a silent hold, and per-address and per-client limits apply. There is no puzzle or timing trap, so assistive technology is not excluded.
- **Mail:** template ZID-EM-001 through the communication estate, logged under the cell's registration tenant. It is sendable only if an operator routes `single_use_verification_url` (for example to `/sign-up/verify`) and enables the template; otherwise nothing is sent and the person can ask again.
- **Migration `onboarding_0003`:** `registration_intent` (guarded: immutable identity fields, final states final, the address written once then only cleared), `registration_account`, `registration_invitation_index`, all tenant-scoped to the registration tenant and recorded in `table-ownership.yaml`. `entrypoints.seed_registration_tenant` creates the tenant once per cell (set `ZID_REGISTRATION_TENANT_ID`).
- **Events** (declared in `proposed-events.yaml`): `onboarding.registration.started`, `.email_initiated`, `.verification_completed`, `.authenticator_enrolled`, `.completed`, `.failed_safe`, `.abandoned`. They are the record's eight instrumentation names without `signup_` and carry no address.
- **Screens:** the sign-up form, verify, enrollment and status states in the web console (`/sign-up`, `/sign-up/verify`).

## Decision taken (by the user, 2026-11-02)
- Pre-account email and requests live in one platform-owned registration tenant per cell; each person gets their own personal tenant when the inbox is proved.
- **Retention:** an ended sign-up request is purged 30 days after it ended (maintenance job; a database guard refuses any earlier delete); open or recent requests are never deleted; the account index (keyed hash to account) lives as long as the account.
- **Held requests:** read-only support lookup only (`entrypoints.registration_hold`); a person never releases a held request, they start a new one.
- **SSO:** none offered for now.
- **Countries:** GB only. The EU and U.S. rows are commented out in `config/registration/country-routing.yaml` until those cells pass their readiness gates; those countries get the honest "not available yet" answer and nothing is stored.

## Known limits, stated plainly
- **Accessibility and responsive behaviour (AC-10)** are covered by a real Chromium run (axe WCAG 2.2 AA at desktop, tablet, 320 px, landscape and 200 percent zoom, 44 px targets, keyboard order, announced errors; `frontend/tests/e2e/signup-a11y.spec.ts`) and by the logic tests. There is no screen-reader run on a real device; that needs QA, and the row stays open until it is done.
- **Passkey enrollment (AC-09)** reuses the existing ticket ceremony. The live end-to-end spec (`frontend/tests/e2e/signup-live.spec.ts`) drives the real form, the real verification mail (read from a local Mailpit sink), the single-use link and a real WebAuthn ceremony with Chromium's virtual authenticator, ending at an active account that is not identity verified. The component tests add the passkey directly. The "approved alternative when a passkey is unavailable" is the governed recovery path, never email or SMS, and its policy is not approved here.
- **Enterprise and social SSO** are not offered: no provider is approved for sign-up (`identity_providers` is empty), and an SSO email claim is never enough for owner or approver authority.
- **Only the newest pending invitation per address is routed**; others stay reachable by their own links.
- **Country registry (ZID-EDR-001):** registration also asks the country capability registry for REGISTRATION. A country with no approved REGISTRATION (a sandbox counts in local and test only) is refused with `COUNTRY_CAPABILITY_UNAVAILABLE` (422) before anything is stored, the capabilities answer says `NOT_AVAILABLE_YET`, and both carry the country's contact (or the default in `country-routing.yaml`). This check runs before the cell-routing check, so a country the registry has closed is never answered with a redirect to another region. Background jobs ask the registry too: the rescreening pass leaves a case alone, and counts it as held, when its country is no longer open for business verification.
- **Country table:** GB only (owners, 2026-11-02). The EU and U.S. cells are not ready (residency owner pack), so their rows are switched off. Legal and Privacy own the table.
- **Rate limits** are in-process per client address plus the per-address count in the database; the edge limiter is the production control.
- **Timing equalization** is best effort: the response does the same database work for every address and mail is sent after it, but no constant-time guarantee is claimed.
- **A held request is silent.** Support has a read-only lookup (`registration_hold`); nothing releases a held request.
- **Legal-notice evidence** is the notice version recorded on the request; consent records beyond it, and marketing consent, are not collected.
- **Local mail sink:** `ZID_SMTP_INSECURE_LOCAL` lets a local stack send to a mail sink without TLS; it is refused outside the local and test environments.

## Completed later (2026-11-03): change the address (state S3) and the event list
- **Change the address.** `POST /v1/registration/intents` takes an optional `replaces`: the request this browser already holds. If it is still open
  (INITIATED or VERIFICATION_SENT) it ends there: its link stops working on every device, its copy of the address is dropped, and one coarse
  `onboarding.registration.abandoned` event says `ADDRESS_CHANGED` (a request id and a reason, never an address). Anything else it names (unknown, malformed, already
  verified, held or ended) is ignored without a word, so it cannot be used to learn whether a request exists, and a confirmed account is never undone by naming it. The
  web proxy fills `replaces` only from this browser's own HttpOnly cookie; what the page body says is ignored.
- **`signup_intent_selected` is not a separate event.** The intent (for myself or for an organization) is chosen in the same submission that starts the request, so
  `onboarding.registration.started` already carries it (`journey`). A second event would say nothing the first does not. Seven events are published for the eight the
  design lists; an owner who wants the eighth as its own name can ask for it.
- Tests: `test_giving_another_address_ends_the_earlier_request_and_drops_its_copy_of_the_address`, `registration.test.ts` (4). AC-10 is linked to the Chromium run
  (`frontend/tests/e2e/signup-a11y.spec.ts`: 320 px, landscape, 200 percent zoom, keyboard order, announced error summary); the screen-reader run on real devices stays QA's.

## Production hardening (2026-11-03)
- **Erasure follows the person.** The account index, the sign-up requests and the invitation routes of an erased account are found, exported without the address hash and
  removed by the privacy erasure (`onboarding/infrastructure/privacy_adapter.py`, registered at start-up in the cell and the worker when a registration tenant is configured).
  Onboarding migration `0007`: the account index gets its own guard and the request guard learns the same exception, so a delete succeeds only inside a transaction
  that the erasure marked (`app.registration_erasure`), which the adapter clears at once; every other delete is still refused, even for the worker, and updates always are.
  The runtime role still has no delete. Before this, an erased person left a pseudonymous link behind and their address could never register again.
- **Rate limits that hold behind an ingress.** The supervisor passes `ZID_FORWARDED_ALLOW_IPS` to uvicorn so the real client address is believed only from named
  proxies (`*` is refused outside local and test); without it every caller behind one ingress shared one bucket. The in-process limiter now forgets clients whose window
  passed and is capped per bucket, so a spray of addresses cannot grow the process without bound.
- **Documents for the people who run it:** `docs/threat-model/signup.md` and `docs/runbooks/signup-operations.md`.
- **Decisions made by engineering for the owners to ratify** (owner pack items 6, 8, 9, 10, 12, 13): no fallback to a passkey weaker than governed recovery; support wording for
  held requests; erasure removes the account index; the nonce is time-limited, not single-use.
- Tests: `test_an_erased_account_takes_its_sign_up_records_with_it_and_only_erasure_may_do_that`, `test_registration_limits.py` (3), a supervisor test.
