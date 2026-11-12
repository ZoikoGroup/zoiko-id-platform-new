# Phase M: Public assistant

> **Source:** Zoiko iD Public AI Chatbot document set, ZI-CHAT-UX-001, KB-002, ENG-003, HO-004, CONTENT-005 (24 Oct 2026) and ZI-CHAT-ADR-006, ODR-007, CSR-008, PRI-009, INT-010 (11 Nov 2026), all v1.0.
> **Status of the source:** design proposals. Every decision in them is OPEN until signed. This phase builds the **non-public prototype** that Doc 07 allows, plus the evidence for gates G0..G4. Nothing here is a launch authorisation.

Back to the [phase index](README.md). Plan: [IMPLEMENTATION_PLAN.md](../AI_Assistance/IMPLEMENTATION_PLAN.md). Evidence: [EVIDENCE_LEDGER.md](../AI_Assistance/EVIDENCE_LEDGER.md) and [TEST_TRACEABILITY.md](../AI_Assistance/TEST_TRACEABILITY.md). Operations: [runbook](../runbooks/public-assistant.md). Threat model: [public-assistant.md](../threat-model/public-assistant.md).

## What it is

An anonymous, **information-only** assistant for a public website. It explains Zoiko iD concepts from approved public content, cites its sources, and routes visitors to official pages. It never signs anyone in, verifies anyone, reads a record, resets anything, or touches a tenant (INV-01). It is built **in the platform repository first** and embedded in the marketing website afterwards (slice M-6).

## Decisions (recorded here, the way the other phases record theirs)

Each is a **proposal for the named owner to ratify**; the code follows it, and the tests listed prove it.

| ID | Decision | Why | Proven by |
|---|---|---|---|
| **DEC-M1** | The assistant is a **separate deployable service** (`backend/assistant`, own image `--target assistant`, port 8210), **not** a module of the monolith. It imports only `zid-app`, `zid-errors`, `zid-telemetry` and never `zid_platform`. | ADR-02, OD-001: a compromised public service must hold no tenant database access or IAM token. A monolith module shares process, keys and credentials. **Needs CTO ratification.** | `tests/security/test_isolation.py` |
| **DEC-M2** | **Its own database** (`zid_assistant`) and **least-privilege role** (`zid_assistant`: no superuser, no create-role, no bypass-RLS, only the `public_assistant` schema). | Defence in depth on DEC-M1; OD-002. | `component/test_postgres_valkey.py::test_the_runtime_role_cannot_reach_the_platform…` |
| **DEC-M3** | Knowledge is **content-as-code** (`content/*.yaml`), compiled into one **digest-pinned, Ed25519-signed snapshot**. A production process **refuses** an unsigned or sample snapshot. | ADR-03, INV-02, CSR-008: only signed, released, source-backed claims. | `unit/test_snapshot.py` |
| **DEC-M4** | Answering is **deterministic first** (safety rules in code, then an approved answer entry), **constrained retrieval second** (lexical BM25 over approved spans), **model last**. | ADR-04. *Deviation from the plan:* retrieval is in-memory BM25 over the compiled snapshot, not Postgres full-text, because the corpus is small and the snapshot is already immutable; revisit when the corpus grows. | `golden/test_golden_corpus.py` |
| **DEC-M5** | **Model gateway** with a provider interface: `mock` by default, **Claude API** behind a key *file*. No tools, no thinking budget, no sampling parameters, no prefill, **no streaming** of unverified text, and **no server-side fallback** to another model. | ADR-05, ADR-10: a refusal or outage must fail closed, never reroute visitor text to an unapproved model or region. Default model `claude-opus-5-5` (current default; a cheaper tier is a one-line setting, OD-017). | `unit/test_verifier_model.py` |
| **DEC-M6** | The model's output is **untrusted** and verified by the server: schema, known citations only, no links/markup/secrets/account assertions, no unapproved availability or assurance wording. | INV-04, ADR-04. | `unit/test_verifier_model.py`, P0-03 |
| **DEC-M7** | **Anonymous sessions** in Valkey (or process memory locally); opaque handle, stored under its hash; 30 min idle / 2 h absolute (provisional, RET-02). No transcript is stored anywhere. | ADR-07, RET-01. | `unit/test_state_stores.py`, P0-01 |
| **DEC-M8** | **Feature flags default OFF** and a **runtime kill switch** (Valkey-shared, no deploy): chat, model and intake stop independently. | INV-07, ADR-14, OD-018/021. | P0-08 |
| **DEC-M9** | **Metadata-only telemetry**: analytics tables have no free-text column; a structural test forbids it. | ADR-12, RET-05. | `test_isolation.py::test_analytics_tables_have_no_free_text_column` |
| **DEC-M10** | The API contract is **service-owned** (`backend/assistant/contract/assistant-v1.openapi.json`, generated from the code), not placed in the shared `contracts/` folder. Doc 10's intake routes live under the same `/api/public/chat/v1` prefix. | The shared folder drives the generated platform client; the assistant is deliberately isolated. | `unit/test_contract.py` |
| **DEC-M11** | The widget is a **reusable package** (`@zoiko/assistant-widget`); each host app adds a thin **server-side proxy** (allowlisted headers, one cookie, no fabricated success). A website, when approved, would add its own thin proxy and a copy of the widget. | ADR-02, ADR-07, ADR-13. | `frontend/apps/web/src/server/assistant-proxy.test.ts`, widget tests |

## Slices

| ID | Slice | Doc 04 packages | Status |
|---|---|---|---|
| **M-1** | Service scaffold, settings, flags, kill switch, sessions, origin/CSRF, strict schemas, quotas, **secret interceptor**, safe link registry, deny tests | B00, B02, B03, B08 | Built, tested |
| **M-2** | P01..P05 registers, signed snapshot, eligibility engine, withdrawal at serving time, 28 draft answers + 18 intents as samples | B04, B05, B07 | Built, tested |
| **M-3** | Intent router, retrieval, answer packet, model gateway + Claude provider, verifier, widget (WF-01..WF-04) | B01, B06 | Built, tested; AI OFF by default |
| **M-4** | Sales/support intake (WF-10A/B), encrypted store, outbox, log-only + webhook adapters, retention and erasure, feedback, runbook | B09, B10 | Built, tested; forms OFF by default |
| **M-5** | Golden corpus, P0-01..P0-08, isolation proofs, Postgres/Valkey component tests, CI, evidence ledger, traceability | B11 | Built; **humans must sign (B12)** |
| **M-6** | Website integration (vendored widget + proxy + mount, off by default) | B12 (part) | **Not done.** A prototype was built and then **removed at the owner's request** (2026-11-11); the website repo is untouched. The platform widget and proxy are reusable when this is requested again |

## Deviations from the documents (all recorded, none silent)

1. **Retrieval** is in-memory BM25, not Postgres full-text (DEC-M4).
2. **Doc 03's `POST /handoffs`** starts the consented flow and returns the form config; Doc 10's `POST /intake/sales|support` submit it. Both are served; the contract lists them.
3. **Rate limiting** keys on the last hop of `X-Forwarded-For` (hashed, per-process salt, never stored) because the host proxy is the only caller. The edge (T1) provides the real limiter in production.
4. **No Alembic stream.** The assistant has a small SQL runner (`python -m zid_assistant.migrate`) so it does not import the platform's migration machinery (DEC-M1).
5. **Intent copy:** where an approved answer entry exists for a refuse/escalate intent, its richer text is used with the intent's decision.
6. **Content is a sample.** All 28 answers are `DRAFT_NOT_PUBLISHED`, all 25 capabilities `EVIDENCE_PENDING`; the UI labels sample content. Production refuses it (DEC-M3).

## Run it locally

```powershell
docker compose up -d postgres                     # with ZID_DB_HOST_PORT=5440 if 5432 is taken
cd backend
Copy-Item assistant\.env.example assistant\.env   # local demo values; every flag defaults OFF without it
python -m uv sync --all-packages
python -m zid_assistant.migrate                   # creates the zid_assistant database, schema and role
python -m zid_assistant                           # http://localhost:8210   (health: /health/ready)
cd ..\frontend; npm run dev:web                   # http://localhost:3000/assistant-preview
```

Tests: `python -m pytest assistant/tests -q` from `backend/` (the component tests need Postgres and Valkey and skip otherwise). Lint and types: `ruff check assistant`, `mypy assistant/src`.

## Gates

G0..G4 evidence and the status of OD-001..OD-024 are in [EVIDENCE_LEDGER.md](../AI_Assistance/EVIDENCE_LEDGER.md). **No gate is signed.** Code can prove its half of each gate; the signatures belong to the named human owners.

## Board-design restyle (widget UI)

The widget follows the design board (`docs/AI_Assistance/WhatsApp Image 2026-10-20 at 22.45.37.jpeg`): navy header with the host's logo, welcome card, four topic tiles, audience chips (Developers / Enterprises / Individuals), timestamped bubbles, round teal Send, and a footer of approved links.

Decisions that differ from, or tighten, the mockup:

- **Status is real.** "Online" is shown only after a successful bootstrap; otherwise Connecting / Offline / Unavailable. It is never hard-coded.
- **No file attach.** The board's paperclip is intentionally omitted (ADR-08 / INV-03: the assistant accepts text only). A test asserts no file control exists.
- **Tiles and audience chips send ordinary visible questions.** No hidden profile or audience field is sent; each maps to an approved answer (tested against the sample library).
- **Minimize vs Close.** Minimize hides the panel and keeps the chat; Close deletes the server session and clears everything.
- **Content stays code-owned.** Tile label/description/icon come from `copy.yaml` (icon is a fixed enum, never markup); footer links come from the destination registry. The sample content includes one new generic source (PUB-018, verifiable credentials), still marked as unapproved sample.
- **Retrieval guard.** A hit must now cover at least half of the question's weighted terms, so one shared common word cannot serve an unrelated answer.

The console guide (phase N) reuses the same widget with its own tiles and the platform logo, and no audience chips.

## Question testing round (what was found and fixed)

About 70 realistic questions were run against the live assistant (Groq model, sample content). Fixed:

- Greetings and thanks ("hi", "thanks") were refused as out of scope: now a friendly reply (whole-message only; "hi, what is pricing" still routes by the question).
- "Is it free?", "uptime guarantee", fees: now reach the pricing intent; "fingerprint login": passwordless intent; "how does it work?" and "available in <country>": clarify or the approved availability answer.
- "Where is my data stored?" no longer says it cannot "look up personal information" (wrong for a general question): wording widened.
- Email addresses and phone numbers typed into chat are now blocked like other personal data (chat only; the consented handoff form still takes an email).
- A "does Zoiko iD support X?" question is no longer answered with a bare definition: the model prompt now requires it to say it cannot confirm support.
- An approved answer on "delegated authority" was wrongly matched to "What is RBAC?": the bad match phrases were removed.
- New verifier gate G6 (grounding): a model answer is rejected when most of its distinctive words are not in the passages it cites.
- Answer text that began "Not without ..." was reworded.

Known limits: the sample library is small, so general identity questions it has no source for (for example "authentication vs authorization", "explain OAuth") abstain or are refused rather than answered. That is the designed behaviour until the real content is approved.
