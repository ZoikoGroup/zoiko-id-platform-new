# Sign-up operations: switching a cell on, checking it, answering a person, removing a person's records

Owner: Zoiko iD operations with Security and Privacy · Applies to person-first sign-up (ZID-UX-WF-013A; CR-048). This is a guide for the people who run a
cell, not a numbered incident runbook. The decisions it leaves to people are in `docs/signoff/signup-owner-pack.md`.

## 1. Switch a cell on (once per cell)

1. **Country.** Sign-up opens only where the country capability registry approves REGISTRATION for the environment, and the country has a home cell in
   `backend/config/registration/country-routing.yaml` whose readiness gates have passed. Today that is GB only. A country that is not open answers "not available
   yet" and stores nothing.
2. **Registration tenant.** `python -m zid_platform.entrypoints.seed_registration_tenant` (a dry run), then again with `--apply`. It is idempotent and prints
   `ZID_REGISTRATION_TENANT_ID=<id>`. Set that value on **both** the cell process and the worker: the worker runs the retention purge and every erasure, and
   without it the sign-up records of an erased account would be left behind.
3. **Database privileges.** The cell runtime role has no delete anywhere. The worker role (BYPASSRLS) must hold DELETE on `platform.registration_intent`,
   `platform.registration_account` and `platform.registration_invitation_index`. The database still refuses any delete that is not an ended request past its
   30 days or part of an account erasure.
4. **Client address.** Behind an ingress, set `ZID_FORWARDED_ALLOW_IPS` to the ingress addresses (comma separated). Without it every caller looks like the ingress
   and shares one limit bucket. `*` is refused outside local and test.
5. **Mail.** Set `ZID_EMAIL_ENABLED=true`, add `ZID-EM-001` to `ZID_EMAIL_ENABLED_TEMPLATES` (or `["*"]`), set `ZID_EMAIL_FROM`, `ZID_EMAIL_PUBLIC_BASE_URL` and the
   SMTP settings, with the password supplied as a secret file. `single_use_verification_url` is routed to `/sign-up/verify` by default. Security and Brand
   approve the sender domain (SPF, DKIM, DMARC) and the copy before production.
6. **Notice.** Set `ZID_TENANT_SIGNUP_NOTICE_VERSION` to the current privacy notice version; a person who saw another version is asked to read the current one and
   nothing is stored.
7. **Edge limits.** Configure the edge rate limiter for `/v1/registration/*` (Security sets the numbers). The in-process limits and the per-address hourly cap are
   a backstop, not the control.

## 2. Check it works

- Send yourself a sign-up with an address you own: the mail arrives, the link opens `/sign-up/verify`, the address bar loses the token, a passkey can be created,
  and the account page says "Active" and "Identity not verified".
- Open the link a second time: "This link can't be used", the same words for every failure.
- Give another address from the same browser: the first link stops working.
- `python -m zid_platform.modules.communication.cli --env-file <settings> coverage` lists ZID-EM-001 as sendable.

## 3. A person says "We couldn't complete registration securely"

The person is told nothing more, and so are you: there is no score to read.
`python -m zid_platform.entrypoints.registration_hold --workspace zoiko-registration --request <request id>` shows the request's state, country, journey, timestamps
and counters, never an address, token or score. It changes nothing.

What support may say (approved wording, engineering proposal for Security to confirm): *"We could not complete this registration. You can try again later, or sign
in if you already have a Zoiko iD. We can't share the reason."* Support must not confirm or deny that an address has an account, must not say the request was
held for a risk reason, and must not offer to release it. Releasing a held request is a Security decision that has not been made; until it is, there is no
release procedure, and a person starts again with a new request when the old one ends.

## 4. A person asks to have their sign-up records removed

An erasure of the person's account (the privacy module) removes the account index, the sign-up requests and any invitation route, in the worker. Check that the
worker has `ZID_REGISTRATION_TENANT_ID` set (step 1.2). Afterwards the address can register again. Nothing else deletes these records: an unmarked delete is
refused by the database even for the worker.

## 5. What to watch

- A rise in `onboarding.registration.failed_safe` events (requests held) means abuse control is firing; a rise in `onboarding.registration.abandoned` with reason
  `ADDRESS_CHANGED` is people mistyping their address and is not an alarm.
- `COUNTRY_CAPABILITY_UNAVAILABLE` and `REGION_UNAVAILABLE` answers show people in countries that are not open yet; Legal and Privacy own the contact route they see.
- Mail failures: the communication estate's message log shows a sign-up message that was not delivered; the person can ask for a new link (limited).
