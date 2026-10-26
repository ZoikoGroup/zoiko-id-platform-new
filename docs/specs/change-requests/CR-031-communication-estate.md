# CR-031: The email communication estate (ZID-EM-001 to ZID-EM-160)

- **Target:** Zoiko iD Email Communication Estate, copybook v2.0 (23 Oct 2026), §02 to §07 · **Raised by:** Phase K · **Status:** Open (engine, catalogue, SMTP delivery, contacts and ten wired templates built; the other 150 templates are catalogued, rendered and tested but wait for a producer event; the release decisions in §07 of the copybook are people's)

The copybook is *copy and rules*, "not evidence of deployed features or legal approval". This CR builds what engineering can build from it and leaves every approval to the people the copybook names.

## What the copybook requires, and where it lives

| Copybook rule | Implementation |
|---|---|
| 160 templates, each with id, event, recipient, class, subject, preheader, body, one action (§06) | `templates/catalogue.json`, produced by `tools/scripts/build_email_catalogue.py` from the copybook; `domain/catalogue.py` loads it, derives a content `version` per template and validates its invariants (unique ids, one-time code only in ZID-EM-009, an action points at a link field) |
| Exact message wrapper: greeting, copy, one CTA with the destination spelled out, sign-off, service or marketing footer (§04) | `domain/render.py`; HTML (responsive, `lang`, one `h1`, dark-mode and narrow-screen rules, hidden preheader) and an equivalent plain-text part |
| Classes SECURITY / SERVICE / PRIVACY / INCIDENT / MARKETING; only marketing is optional (§03, §05) | `catalogue.classify`. ZID-EM-143, 144 and 145 carry a "Marketing" label but their own engineering controls say they confirm the person's own action, so they are SERVICE |
| Fail closed on an unresolved placeholder; escape; max length; reject merge-tag injection (§05) | `RenderError` codes `MERGE_MISSING`, `MERGE_UNKNOWN`, `MERGE_TOO_LONG`, `MERGE_INVALID`; nothing is sent |
| Links from a signed first-party HTTPS allowlist, never a Host header or a caller URL; single-use links carry only an opaque token (§03 SAFE LINKS) | `LinkPolicy`: a bare HTTPS origin and plain paths; a route field takes at most a plain object id, a token field an opaque token; no route configured means the template is not sendable |
| No password, key, document, credential, raw token, card number in a body; a code only in ZID-EM-009 (§03 SENSITIVE DATA) | test over every placeholder; the code has a strict shape; `Recipient` and `Rendered` hide addresses and bodies from `repr` |
| Avoid user content in high-risk subjects (§03 PRIVACY) | a SECURITY, INCIDENT or PRIVACY header with a link or an address in it is refused (`HEADER_UNSAFE`); headers are single-line |
| Trigger from durable domain events; re-check recipient, tenant, suppression and state just before sending (§03 SOURCE OF TRUTH) | `application/dispatch.py` maps outbox events to templates; the consumer handler plans inside the tenant's transaction and sends outside it |
| Event-level idempotency, retry/backoff, per-recipient flood protection (§03 EMAIL TRANSPORT) | `platform.email_message` unique on (tenant, event, template, recipient); bounded retry on temporary failures; a FAILED row is retried by the next delivery of the event; a PENDING row only after a lease; per-template and per-recipient hourly limits |
| Bounce handling and suppression; marketing consent and one-click unsubscribe, separate from security mail (§02, §03) | a permanent refusal of an address becomes a `BOUNCE_HARD` suppression in every tenant; marketing needs a recorded consent and carries RFC 8058 `List-Unsubscribe` headers; the public `/email/unsubscribe` link is signed and answers the same for a known and an unknown address; no unsubscribe or complaint ever suppresses a mandatory class |
| Log event id, template id and version, status and correlation, never tokens, URLs or addresses (§03 OBSERVABILITY) | the message log holds exactly that, plus a keyed hash of the recipient |
| TLS (§03) | STARTTLS on 587 or implicit TLS on 465 with certificate and hostname verification; the login is never sent in clear |
| Locale, time zone and money formatting; safe fallbacks (§03 LOCALIZATION) | US-English master format in the recipient's zone; a bad time zone falls back to UTC; money is currency-aware |
| Snapshot tests of subject, preview, HTML and text for all 160 templates (§05) | `email_snapshots.json`, regenerated only on purpose (`ZID_UPDATE_SNAPSHOTS=1`) |

## Tables (communication module, `platform` schema)

| Table | Scope | Note |
|---|---|---|
| `platform.email_message` | tenant-scoped (RLS) | One row per (tenant, event, template, recipient): template id and content version, class, status (`PENDING`, `SENT`, `FAILED`, `SUPPRESSED`, `THROTTLED`, `RENDER_FAILED`), a reason code, attempts, a keyed recipient hash. No address, subject, body, merge value, token or code. |
| `platform.email_suppression` | global | Recipient hash, reason (`BOUNCE_HARD`, `COMPLAINT`, `UNSUBSCRIBED`), optional list. Global because a bounce or an unsubscribe belongs to the address and the unsubscribe endpoint has no tenant. |
| `platform.email_consent` | global | Marketing consent per recipient hash and list, with the public basis shown in the footer. |
| `platform.email_contact` | tenant-scoped (RLS) | Where mail goes. The address is **not** here: it is an object in the evidence vault, encrypted under the person's erasable subject key (their own contact) or the cell data key (an organization contact), and erased with the subject. |

## Where addresses live, and why

The platform holds only a keyed *hash* of an identifier (`core.principal_identifier`; `encrypted_value_ref` is never populated), so it cannot mail a principal from what it already has, and events carry ids, not names or addresses. Rather than weaken that, deliverable addresses are held only in `platform.email_contact` plus the vault, set deliberately by a person (`PUT /v1/account/contact`) or an administrator (`PUT /v1/tenant/contacts`: billing, security, technical, owner). Reading an address back decrypts it only at send time.

## API

| Route | Action | Note |
|---|---|---|
| `GET /v1/tenant/contacts` | `communication.read` (R1) | the organization's contacts, masked (`j***@example.com`) |
| `PUT /v1/tenant/contacts` | `communication.contact.manage` (R2) | add a billing, security, technical or owner contact; idempotent; refuses anything that could carry a header or a second recipient |
| `DELETE /v1/tenant/contacts/{id}` | `communication.contact.manage` | retire one |
| `PUT /v1/account/contact` | `communication.contact.manage` | where the caller's own security notices go (replaces the previous one) |
| `GET /email/unsubscribe` | public | a confirmation page; a GET changes nothing |
| `POST /email/unsubscribe` | public | RFC 8058 one-click; keyed signature over recipient hash and list |

## Wired templates (events this platform emits today)

ZID-EM-023 and 024 (`authenticator.registered`, `authenticator.revoked` → the person's own contact), ZID-EM-039 (`credential.issued`; stays unsendable until a wallet route is configured), ZID-EM-082 (`agent.suspended` → owner contacts), ZID-EM-127 and 128 (`commercial.entitlement.changed`), ZID-EM-129 (`commercial.budget.threshold_crossed`), ZID-EM-130 (`commercial.invoice.issued`), ZID-EM-131 and 132 (`commercial.payment.recorded`, now carrying amount and currency; invoice and payment events carry the invoice id). `coverage()` and `cli coverage` list every template, its event and whether it is wired.

The other 150 are in the catalogue, render, and are covered by the snapshot test, but **nothing sends them**: the flow that produces their event does not exist yet, or the platform's event has no addressable recipient (for example invitations, where `POST /v1/tenant/members` takes no email, and sign-up, which collects none). Sending a template for an unimplemented flow is exactly what copybook §07 "launch scope" forbids.

## Release switches (copybook §07), all safe by default

| Setting | Default | Meaning |
|---|---|---|
| `ZID_EMAIL_ENABLED` | `false` | master switch; nothing is sent while off |
| `ZID_EMAIL_ENABLED_TEMPLATES` | `[]` | JSON list of template ids, or `["*"]` for every template that is wired and routable |
| `ZID_EMAIL_FROM` | unset | `Name <address>` of a sender whose domain has SPF, DKIM and DMARC aligned |
| `ZID_SMTP_HOST`, `_PORT`, `_USERNAME` | unset, 587 | the relay |
| `ZID_SMTP_PASSWORD_FILE` | unset | a file holding the password, never an inline value (DEC-F5) |
| `ZID_EMAIL_PUBLIC_BASE_URL` | the public base URL | the one HTTPS origin links may point to |
| `ZID_EMAIL_ROUTES` | `{}` | extra link-field → path routes (support centre, status centre, preference centre, wallet and so on have none by default, so the templates that use them stay unsendable) |
| `ZID_EMAIL_FLOOD_PER_TEMPLATE_HOUR`, `_PER_RECIPIENT_HOUR` | 5, 20 | flood limits |

`python -m zid_platform.modules.communication.cli [--env-file F] coverage | render ID | send-test ID --to ADDRESS` renders a template with obvious sample values or sends one test message to exactly the address given, to prove the transport. It touches no database.

## Still people's decisions (copybook §07)

Sending domain and branding (Security + Brand), legal roles and notices (Legal + Privacy), high-risk assurance rules (CISO + IAM), link expiry and redirect policy (Security + Engineering), billing terms (Commercial + Finance), incident and regulatory duties (Security + Legal), localized release (Product + Accessibility), launch scope (CTO + Product). Local development currently relays through a personal Gmail account, which is **not** a production sender: it has no Zoiko domain, is limited to a few hundred messages a day and cannot meet the sender-authentication requirement.

## Known limits

- Delivery retry is bounded (three attempts over about 20 seconds) and a message is not stored, so a long provider outage fails those messages (visible as `FAILED` rows); the event can be re-emitted. Persisting an encrypted outbox is a later step.
- A crash between sending and recording leaves a PENDING row; after the lease the next delivery of the event may send once more (one duplicate at worst, never a lost security notice).
- Complaint (spam report) and asynchronous bounce intake need a provider webhook; Gmail's relay has none. `add_suppression` is the single entry point when one exists.
- Only the US-English master is rendered; other locales need reviewed translations (copybook §01).
