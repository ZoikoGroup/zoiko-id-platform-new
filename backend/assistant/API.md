# Public assistant API v1 (service-owned contract)

Base path: `/api/public/chat/v1`. JSON UTF-8 only; no multipart, no uploads. Served by `backend/assistant` (port 8210 locally).
Reconciles ZI-CHAT-ENG-003 section 04 (7 routes) and ZI-CHAT-INT-010 section 05 (intake routes). The machine-readable
OpenAPI is exported from the code to `contract/assistant-v1.openapi.json` and a test keeps it in step with this file.

## Who calls it
Browsers never call this service directly. A **host app** (the platform web app now, the website later) exposes a same-origin proxy
`/api/public/chat/v1/*` that forwards to the assistant. The proxy MUST:
- forward only the allowlisted headers: `content-type`, `accept`, `accept-language`, `idempotency-key`, `origin`, and the **one** cookie `zid_pa_session`
  (never any other cookie, never `authorization`, never `x-forwarded-user`-style headers);
- relay `Set-Cookie` for `zid_pa_session` only (host-only, `HttpOnly`, `SameSite=Lax`, `Path=/api/public/chat/v1`);
- return **503 `UNAVAILABLE`** when the assistant is unreachable. It must never fabricate an answer or a success.

The assistant checks `Origin` against its allowlist (`403 ORIGIN_DENIED`) on every state-changing request.

## Error shape (all non-2xx)
```json
{ "error": { "code": "INVALID_REQUEST", "message": "Human-readable, never echoes input.", "correlation_id": "cor_...", "retry_after_s": null } }
```
Codes: `400 INVALID_REQUEST` · `401 INVALID_GUEST_SESSION` · `403 ORIGIN_DENIED` · `403 REGION_OR_POLICY_BLOCK` · `404 NOT_FOUND` ·
`409 IDEMPOTENCY_CONFLICT` · `413 PAYLOAD_TOO_LARGE` · `415 UNSUPPORTED_MEDIA_TYPE` · `422 SENSITIVE_CONTENT_BLOCKED` · `429 RESOURCE_LIMIT` · `503 UNAVAILABLE`.
A restricted answer is **not** an error: it is `200` with `decision` = `REFUSE` / `ABSTAIN`.

## Routes

### GET `/bootstrap`
No session needed. Cacheable (`ETag`).
```json
{
  "enabled": true,
  "display_name": "Zoiko iD Assistant",
  "locale": "en-US",
  "revision": "pub-policy-1",
  "snapshot_id": "sample-2026-11-11",
  "sample_content": true,
  "flags": { "ai_answers": false, "intake_sales": false, "intake_support": false, "marketing": false },
  "limits": { "message_max_chars": 1000, "session_idle_seconds": 1800 },
  "starters": [ { "label": "Verifiable credentials", "text": "What are verifiable credentials?", "description": "Digital identity, KYC and trusted data sharing.", "icon": "document" },
                { "label": "What is Zoiko iD?", "text": "What is Zoiko iD?" } ],
  "static_help": [ { "link_id": "OVERVIEW", "label": "Platform overview" } ],
  "footer_links": [ { "link_id": "TALK-TO-EXPERT", "label": "Talk to an expert" } ],
  "notice": "Do not enter passwords, one-time codes, identity documents or personal records. This assistant is public and cannot access accounts."
}
```
A starter with an `icon` (one of a fixed set: lock, document, shield, share, code, building, user, chat) and `description` is shown as a topic tile; the rest are plain chips. Neither field carries markup, and a starter only ever sends its visible `text` as an ordinary question. `footer_links` are approved, non-secure destinations from the registry.

When `enabled` is false (flag off or kill switch) the widget shows only `static_help` links and no chat input.

### POST `/sessions` → 201
Creates an anonymous session and sets the `zid_pa_session` cookie. Body: `{ "locale": "en-US" }` (optional).
```json
{ "session_expires_at": "2026-11-11T12:30:00Z", "idle_seconds": 1800 }
```

### DELETE `/sessions` → 204
Forgets the session (server state and cookie). Idempotent.

### POST `/messages` → 200 (session required)
```json
{ "client_message_id": "uuid-v4", "text": "How does OIDC differ from OAuth?", "locale": "en-US", "intent_hint": null }
```
`text`: 1..1000 chars. `intent_hint` is untrusted and optional. Unknown keys are rejected (`400`). Same `client_message_id` + same text replays the
same packet; same id with different text is `409 IDEMPOTENCY_CONFLICT`. Response = **AnswerPacket**:
```json
{
  "response_id": "rsp_...",
  "policy_version": "pub-policy-1",
  "snapshot_id": "sample-2026-11-11",
  "decision": "ANSWER",
  "intent_id": "INT-03",
  "answer_text": "OAuth 2.0 is primarily an authorization framework ...",
  "claims": [ { "claim_id": "CL-03", "support": ["PUB-007#s1"] } ],
  "citations": [ { "source_id": "PUB-007", "revision": "r0", "title": "OAuth, OIDC and SAML", "span_id": "s1", "link_id": "DEVELOPER-DOCS" } ],
  "capabilities": [],
  "actions": [ { "type": "OPEN_APPROVED_LINK", "link_id": "DEVELOPER-DOCS", "label": "Developer documentation" } ],
  "safety": { "reason": "OK", "sensitive_input_blocked": false, "sample_content": true, "ai_assisted": false },
  "session_expires_at": "2026-11-11T12:30:00Z"
}
```
`decision`: `ANSWER | CLARIFY | ABSTAIN | REFUSE | ESCALATE`. Action types: `OPEN_APPROVED_LINK` (use `/out/{link_id}`), `START_HANDOFF_SALES`,
`START_HANDOFF_SUPPORT`. The widget renders `answer_text` as **escaped plain text** (no HTML, no Markdown) and shows only server-issued links.
When `safety.sample_content` is true the widget shows a visible "Engineering sample content, not approved" label.
A message containing a secret or identity data returns `422 SENSITIVE_CONTENT_BLOCKED` and nothing about it is stored or echoed.

### POST `/feedback` → 204 (session required)
`{ "response_id": "rsp_...", "rating": "USEFUL" | "NOT_USEFUL" }`. No free text.

### GET `/out/{link_id}` → 302
Validates the link id against the approved destination registry, then redirects. Unknown/suspended: `404`. Never takes a URL.

### GET `/intake/config`
```json
{ "sales": { "available": false, "form_revision": null }, "support": { "available": false, "form_revision": null }, "marketing": { "available": false } }
```
When available: `{ "available": true, "form_revision": "intake-sales-v1", "notice_id": "...", "notice_text": "...", "fields": [ { "name": "email", "type": "email", "required": true, "max_length": 254 } ], "inquiry_types": ["DEMO", "SALES"] }`.
Unavailable means the widget hides every form and shows the static contact links instead.

### POST `/handoffs` → 200 (session required)
`{ "purpose": "SALES" | "SUPPORT" }`: starts the separate, consented contact flow. Returns the purpose's config block above, or `403 REGION_OR_POLICY_BLOCK` when the purpose is off. The conversation transcript is never attached.

### POST `/intake/sales` and POST `/intake/support` → 202
Header `Idempotency-Key: <uuid>` required. Body:
```json
{ "form_revision": "intake-sales-v1", "purpose": "sales_inquiry", "country_code": "US", "locale": "en-US",
  "notice_accepted": true, "fields": { "email": "a@b.test", "inquiry_type": "DEMO", "message": "..." } }
```
Response 202: `{ "reference": "ZR-8F3K2M9Q", "status": "ACCEPTED" }`. The reference is shown **only after durable acceptance** and is not a promise of delivery or an SLA.
Errors: `400 INVALID_REQUEST`, `403 REGION_OR_POLICY_BLOCK`, `409 IDEMPOTENCY_CONFLICT`, `413`, `422 SENSITIVE_CONTENT_BLOCKED`, `429`, `503`.
Error messages are generic: they never reveal whether an address is known.

### POST `/preferences/marketing` → 204
`{ "channel": "EMAIL", "election": true, "country_code": "US", "notice_id": "..." }`. Independent of intake; OFF unless the flag and policy are live.

## Health (service shell, not under the prefix)
`GET /health/live`, `GET /health/ready` (the standard platform shell).
