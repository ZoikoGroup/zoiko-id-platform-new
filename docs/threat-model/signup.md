# Threat model: person-first sign-up (ZID-UX-WF-013A; slice L-11, CR-048)

Status: engineering draft for Security, Privacy and QA to adopt or amend (`docs/signoff/signup-owner-pack.md` item 12). It covers the public sign-up routes
(`/v1/registration/*`), the emailed single-use link, the new account's first passkey, and the data kept along the way. It does not cover the passkey ceremony itself
(`test_authn_pg`, `test_step_up_pg`) or company verification (`docs/signoff/company-verification-owner-pack.md`).

## What is protected

| Asset | Why it matters |
|---|---|
| The address a person typed | Personal data; also proof of an inbox, never proof of identity |
| Whether an address already has an account | Knowing it lets an attacker pick targets |
| The emailed link and the enrollment ticket | Whoever holds one can finish creating the account and enroll a passkey on it |
| The account index (keyed address hash to account) | A pseudonymous link between an address and a person |
| The cell a person's records are created in | Residency promise: nothing is stored in the wrong region |

## Threats, controls and the test that proves each

| # | Threat | Control | Proof |
|---|---|---|---|
| 1 | **Enumeration**: learn whether an address is registered, flooded or held | One answer, one message and one public status for a new, known, flooded or held address; the work done is the same and mail is sent after the answer | `test_registration_pg.py::test_a_known_and_an_unknown_address_get_indistinguishable_answers` |
| 2 | **Replay or theft of the link**: reuse, guess, use on another request, use after expiry | 256-bit token kept only as a hash, single use, 30 minutes, bound to its request, every failure is the same message, attempts are counted; a newer link or a resend kills the older | `test_the_link_is_single_use_time_limited_and_bound_to_its_request`, `test_resends_are_limited_and_end_the_earlier_link` |
| 3 | **Link leaks** through referrer, search indexing, browser history or logs | The verify page sends no referrer and is not indexed, the token leaves the address bar as soon as it is redeemed, the ticket travels in an HttpOnly cookie never in web storage, and no event, audit record or log carries an address, token or URL | `test_no_address_reaches_an_event_or_an_audit_record_and_it_is_cleared_after_proof`; `app/(auth)/sign-up/verify/page.tsx` |
| 4 | **Wrong address typed**, so a stranger's inbox holds a live link | Giving another address from the same browser ends the earlier request: its link dies on every device and its copy of the address is dropped | `test_giving_another_address_ends_the_earlier_request_and_drops_its_copy_of_the_address`, `server/registration.test.ts` |
| 5 | **Race or double submit** makes two accounts for one address | Idempotency keys, superseded links and a unique account index; two confirms make one account | `test_refresh_double_clicks_and_races_cannot_make_duplicates` |
| 6 | **Injection and malformed input** (address, country, journey, locale) | Strict parsing: approved canonicalization with no rewriting of tags or dots, ISO country, closed journey list, BCP 47 tag; the server derives tenant, role, assurance, status and region and never takes them from the caller | `tests/unit/test_registration.py`, `test_a_person_signs_up_into_an_active_account_that_is_not_verified` |
| 7 | **Wrong region**: personal data stored where the person's country does not allow | The country is resolved to a home cell before anything is stored; a country with no cell, or a cell that has not passed its readiness gates, stores nothing; nothing defaults to GB | `test_the_region_is_resolved_before_anything_about_the_person_is_stored`, `test_a_country_the_registry_has_closed_stores_nothing_and_says_where_to_go` |
| 8 | **Flooding and bots**, including using sign-up to mail-bomb a victim | Signed, time-limited nonce (a missing, tampered or expired one is held silently; it is not single-use, so a harvested nonce is bounded by the limits below, not by itself); per-client limits in the process (a backstop that sees the real client only when the ingress is trusted, and stays bounded in memory); per-address hourly cap in the database; per-template and per-recipient flood policy in the mail estate; resends capped | `test_a_request_without_a_valid_nonce_is_held_without_telling_anyone_why`, `tests/unit/test_registration_limits.py`, `tests/unit/test_supervisor.py` |
| 9 | **Account collision and invitation confusion**: an invited person is routed to, or learns of, an organization | An invited address is routed to its invitation only after the address is proved, the organization is named only after authentication, and the person cannot choose a role | `test_an_invited_person_is_routed_to_their_invitation_and_cannot_pick_a_role` |
| 10 | **Sign-in provider confusion**: an SSO email claim treated as proof | No provider is offered (ruled none, 2026-11-02) and the capabilities answer says so | `test_a_person_signs_up_into_an_active_account_that_is_not_verified` (`identity_providers == []`) |
| 11 | **Organization intent mistaken for a verified company** | The organization journey ends in a provisional claim; no Owner, no verified status | `test_organization_intent_makes_no_verified_organization_and_no_owner` |
| 12 | **A weaker second factor** offered as a fallback (email or SMS code) | None exists. The only fallback to a passkey is the governed recovery path, which needs independent approval | capabilities answer `fallback: GOVERNED_RECOVERY` |
| 13 | **Data kept too long, or never removable** | The address is held only until proved or expired, then only a keyed hash; ended requests are purged after 30 days; an erased account takes its account index, requests and invitation routes with it, and only the erasure can delete them (the database refuses every other delete, even by the worker) | `test_an_abandoned_request_ends_and_drops_the_address_and_a_person_can_start_again`, `test_ended_requests_are_purged_after_thirty_days_and_nothing_else`, `test_an_erased_account_takes_its_sign_up_records_with_it_and_only_erasure_may_do_that` |
| 14 | **A forged request origin** (cross-site) | The web proxy accepts only same-origin posts and forwards only the fields each call takes | `app/api/registration/*`, `server/registration.test.ts` |

## Residual risks, and who decides

- **Timing** differences between a known and an unknown address are equalized in the work done, not guaranteed constant-time. Security decides whether that is enough.
- **An attacker who controls the victim's inbox** can finish sign-up for that address. That is what an email address proves; the account is active but not identity verified and holds no organizational authority.
- **The edge limiter** is the production control for flooding; the in-process limits are a backstop. Security sets the edge limits and any further bot control, which must not exclude assistive technology.
- **A held request** is silent by design. Support can read its state, never a score; releasing one is a decision no one has made.
- **Mail delivery** depends on the sender identity (SPF, DKIM, DMARC for the sending domain), which Security and Brand approve.
