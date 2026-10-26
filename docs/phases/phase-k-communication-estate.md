# Phase K: Communication estate (email)

> **Source:** Zoiko iD Email Communication Estate, copybook v2.0 (23 Oct 2026), 160 templates ZID-EM-001 to ZID-EM-160. **Status of the source:** copy ready for engineering integration; "not evidence of deployed features or legal approval". Design and decisions: [CR-031](../specs/change-requests/CR-031-communication-estate.md).

Back to the [phase index](README.md).

## Principles taken from the copybook (and enforced in code)

1. **Events, not requests, send email.** Only a durable domain event triggers a message, and the recipient, tenant and state are re-checked just before sending (§03).
2. **Nothing sensitive in an email.** No secret, key, document, credential, raw token or card number in any body; a one-time code only in ZID-EM-009; links open the console and never perform a protected action (§03).
3. **Links are an allowlist.** One HTTPS origin and plain paths; no caller-supplied URL; a template whose link has no route is not sendable.
4. **Optional is only marketing.** An unsubscribe or a complaint never suppresses a security, service, privacy or incident message; a hard bounce suppresses an address everywhere.
5. **Never send for an unbuilt flow.** A template is sent only when its event is emitted by this platform and a person has enabled it (§07 "launch scope").

## Slices

| ID | Slice | Core deliverable |
|---|---|---|
| K-1 | **Template registry and renderer** | 160 templates from the copybook, content-versioned; the exact wrapper; HTML and text; fail-closed merge; link allowlist; snapshot of all 160 |
| K-2 | **Delivery** | SMTP over verified TLS, bounded retry, event-level idempotency, delivery log without personal data |
| K-3 | **Policy** | Hard-bounce suppression, marketing consent and RFC 8058 unsubscribe, flood limits |
| K-4 | **Contacts** | Addresses held only in the evidence vault, encrypted and erasable; organization and personal contacts; masked API and console screen |
| K-5 | **Event dispatch** | Consumer handler; ten templates wired to events this platform emits (billing, authenticators, agents, credentials) |

## Status

| Slice | Status | Evidence |
|---|---|---|
| K-1 | Built | `test_email_estate.py` (19 tests: whole catalogue, wrapper, every negative case the copybook lists, the 160-template snapshot) |
| K-2 | Built | `test_email_smtp.py` (8 tests, `smtplib` replaced by a recorder), `test_email_pg.py` (idempotency, retry, lease); a real test message was relayed through the configured SMTP account |
| K-3 | Built | `test_email_pg.py` (suppression, consent, unsubscribe, flood) |
| K-4 | Built | `test_email_pg.py` (ciphertext at rest, validation, tenant scope, HTTP API); web screen `/admin/contacts` |
| K-5 | Built for 10 of 160 | `test_email_pg.py` (events to templates, once, email off sends nothing); `coverage()` |

## What this phase does not do

- It does not decide the sending domain, SPF/DKIM/DMARC, legal roles and notices, link expiry, billing terms, incident duties, locales or launch scope: those are the owners named in copybook §07.
- It does not send the other 150 templates: their producing flows (invitations, sign-up, recovery, consent, mandates, support, incidents, marketing) either do not exist or hold no addressable recipient yet.
- It does not persist a retry queue: a long provider outage fails those messages visibly (see CR-031, known limits).
- It does not make email a sign-in factor or an approval channel: email is "not a phishing-resistant authenticator" (copybook §03), and every link only opens the console.
