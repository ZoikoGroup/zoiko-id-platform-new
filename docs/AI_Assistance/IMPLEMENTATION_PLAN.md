# Zoiko iD Public Assistant: Implementation Plan

| Field | Value |
|---|---|
| Plan ID | ZI-CHAT-PLAN (engineering plan, not a numbered design document) |
| Date | 2026-11-11 |
| Built from | ZI-CHAT-UX-001, KB-002, ENG-003, HO-004, CONTENT-005, ADR-006, ODR-007, CSR-008, PRI-009, INT-010 (all v1.0, Oct to Nov 2026), checked against this repository |
| Status | **Built as a non-public prototype (2026-11-11).** Phases 1 to 5 are implemented and tested (phase 6, the website, was reverted at the owner's request); nothing is signed or launched. See the [phase record](../phases/phase-m-public-assistant.md), the [evidence ledger](EVIDENCE_LEDGER.md) and [test traceability](TEST_TRACEABILITY.md). D1 (separate service) was followed and still **needs CTO confirmation**. |

**Scope in one sentence:** build an anonymous, public, information-only assistant in this platform first (a backend service plus a reusable widget), then embed it in the marketing website (`Zoiko-ID-Website`) in the last phase. It never touches accounts, tenants, passkeys or verification.

---

## 1. What the ten documents give us

| Doc | Gives | We use it as |
|---|---|---|
| 01 UX | 4 surfaces WF-01..04, AC-01..15, 10 journeys | Widget and conversation UI. No new screens. |
| 02 Knowledge | 18 intents, CW-01..04, answer packet, source gates, T01..T20 | Router, answer contract, regression corpus |
| 03 Integration | 7 public routes, trust zones T0..T6, errors, IT-01..IT-20, G0..G4 | API v1 and security tests |
| 04 Handoff | Work packages B00..B12, 5 waves, TR-01..11, O01..O08 | The backbone of the phases below |
| 05 Content | 28 draft answers (KB-001..028), all `DRAFT_NOT_PUBLISHED` | Sample corpus, never published as is |
| 06 ADR | 14 decisions ADR-01..14, 7 invariants INV-01..07, P0-01..08 | Design constraints and negative tests |
| 07 Decisions | 24 open items OD-001..024, status machine, gate mapping | Tracks what is blocked on people, not code |
| 08 Registers | P01 capabilities, P02 sources, P03 links, P04 claims, P05 commercial facts; eligibility logic | Knowledge layer (Phase 2) |
| 09 Privacy | Data classes D0..D4, retention RET-01..12 (proposals), privacy workflows | Retention config, redaction, deletion |
| 10 Intake | Sales and support fields, 4 intake routes, outbox and receipt states, 36 tests | Handoff (Phase 4), disabled by default |

### Requirements that shape the build (the non-negotiables)
1. **Public information only (INV-01, ADR-01).** No authentication, verification, record access or identity action, ever. The assistant holds no IAM credentials.
2. **No release claim without evidence (INV-02, ADR-03).** "Available" needs a signed P01 capability *and* a P02 source *and* matching market, audience and locale. Otherwise the answer is ABSTAIN.
3. **Pre-model protection (INV-03, ADR-08).** Secrets and ID content are rejected in memory before logging, embedding or any model call. No file upload.
4. **The model has no authority (INV-04, ADR-04/05).** The server decides ANSWER / CLARIFY / ABSTAIN / REFUSE / ESCALATE. No tools, no streaming of unverified text.
5. **Consent is separate (INV-05, ADR-09).** Intake is a separate form, never the transcript.
6. **Global by design (INV-06, ADR-10).** No UK-only assumption, no inferred regional claims.
7. **Independent shutdown (INV-07, ADR-11/14).** Chat, content and handoff each have a kill switch. The website works without them.
8. **Zero persistence of raw chat (RET-01).** Session state is short-lived and unlinkable (30 min idle / 2 h absolute, proposals).

### Things the documents say themselves that we must respect
- Everything in Docs 06 to 10 is **proposed, not signed**. All 24 decisions are OPEN. Doc 07 allows **non-public prototypes in parallel**, and nothing goes public until gates G0..G4 are signed. This plan builds the prototype and the evidence, with every production capability behind a flag that is OFF.
- Doc 08 found that the live marketing text is inconsistent as evidence of what is released. **No capability is marked RELEASED**, so the prototype can explain concepts but must not claim product availability.
- All routes, table names and paths in the docs are illustrative and must be reconciled with this repo (the docs say so themselves).

---

## 2. What the repository gives us (checked, not assumed)

- `backend/` is a **uv workspace** (`members = ["app", "libs/*"]`) with one modular monolith (`app`, 29 modules) and shared `zid-*` libraries (app shell, errors, telemetry, idempotency, db, outbox, and others).
- Modules follow `ports.py / api / application / domain / infrastructure`. `tests/unit/test_architecture.py` fails the build on cross-module imports, and a plane map and table-ownership registry (CI-checked) govern every table.
- `onboarding/api/registration.py` is the closest precedent: a **public, unauthenticated, off-unless-configured** API with in-process rate limiting, wired in `main.py` through `app.state`.
- `egress.py` already guards outbound fetches (https only, no IP literals, no private ranges, no redirects). The model call must go through the same kind of control.
- `docker-compose.yml` already has `postgres`, `valkey` (port 6380) and the `backend` service. `contracts/openapi/public/` exists. CI has static, unit and component jobs.
- The platform web app (`frontend/apps/web`) is a backend-for-frontend (BFF) where the Next.js server calls the cell API. The website (`Zoiko-ID-Website`) is a separate Next 16 app, deployed to a VM, with no backend calls today.

---

## 3. Design decisions (with the reason for each)

| # | Decision | Why |
|---|---|---|
| **D1** | **A separate deployable service**, `backend/assistant/` (new uv workspace member), **not a module inside the monolith.** Own process, own container, own DB role and schema `public_assistant`, port 8210 locally. It imports only `zid-*` libraries, never `zid_platform`. | ADR-02, INV-01 and OD-001 require that a compromised public service holds no tenant DB access and no IAM tokens. A module inside the monolith shares its process, keys and database credentials. **Needs CTO confirmation.** |
| D2 | Session state in **Valkey** (TTL), not Postgres | Docs 03/09: ephemeral, no raw transcript. Valkey is already in compose. An in-memory fallback is used for tests. |
| D3 | **Knowledge as content-as-code:** P01..P05 and KB answers as versioned YAML in `backend/assistant/content/`, compiled in CI into one signed snapshot, then projected into Postgres | Matches the repo's `backend/config/` pattern and Doc 08's "one revisioned signed snapshot, fail closed". Reviewers use CODEOWNERS. |
| D4 | Retrieval = **Postgres full-text search** over approved P02 spans; deterministic router first, retrieval second | ADR-04. No vector database needed at v1, and fewer moving parts. |
| D5 | Model gateway with a provider interface: **`mock` first, then Claude API** (server-side key only). No tools, no memory, no streaming (ADR-05). Real model stays OFF until OD-010/016/017 are decided | The model vendor decision (O06) is open. We build everything around it first. |
| D6 | Widget as a **reusable package** (`frontend/packages/assistant-widget`) plus a public preview route in `frontend/apps/web`; each host app adds its own thin server-side proxy route | The website must embed the same widget later. The key and cookies stay server-side (ADR-02, ADR-07). |
| D7 | All behaviours behind **feature flags, default OFF**: `assistant_enabled`, `assistant_ai_answers`, `assistant_intake_sales`, `assistant_intake_support`, `assistant_marketing`. Plus a global kill switch. | INV-07, ADR-14, OD-018/021 |
| D8 | **Metadata-only telemetry** (route, verdict, source IDs, timings). Raw text never logged. A log-scan test enforces it. | ADR-12, RET-05, PR-02 |

---

## 4. Target layout

```
backend/assistant/                 uv member "zoiko-id-public-assistant"
  pyproject.toml
  src/zid_assistant/
    main.py  settings.py           ZID_ASSISTANT_* settings; flags; kill switch
    api/                           bootstrap, sessions, messages, feedback, handoffs, out/{link_id}
    domain/                        verdicts, answer packet, intent ids, eligibility rules (pure)
    application/                   router, retrieval, verifier, handoff, retention jobs
    infrastructure/                valkey sessions, postgres repos, model gateway, link resolver, outbox worker
  content/                         p01_capabilities/ p02_sources/ p03_destinations/ p04_claims/ p05_commercial/ answers/
  migrations/                      own Alembic stream, schema public_assistant
  tests/                           unit · contract · component · security (deny + secrets) · golden
contracts/openapi/public/assistant-v1.yaml
frontend/packages/assistant-widget/
frontend/apps/web/src/app/(auth)/assistant-preview/   unauthenticated preview page + proxy route
docs/phases/phase-m-public-assistant.md               the decision record (ADR set) for this repo
```

---

## 5. The six phases

Each phase ends with something runnable and tested. Package IDs (B00..B12) are from Doc 04, decisions (OD) from Doc 07.

### Phase 1: Foundation and secure skeleton (B00, B01 shell, B02, B03, B08 partial; Wave 0 and 1)
**Goal:** a deny-by-default public service with sessions, secret protection and safe links. No AI, no knowledge.
- Scaffold `backend/assistant/`, compose service, DB role and schema, CI wiring, ADR record in `docs/phases/`.
- `contracts/openapi/public/assistant-v1.yaml`: bootstrap, sessions (create/delete), messages (stub), feedback, `out/{link_id}`.
- Guest sessions (opaque, host-only HttpOnly cookie, TTL), Origin and CSRF checks, schema that **rejects extra keys** (no `tenant_id`, token), per-client quotas.
- **Secret and ID interceptor** before any logging or model call (ADR-08).
- P03 link registry (allowlisted HTTPS, no query-controlled redirects) and the static help fallback.
- Widget shell (WF-01: launcher, welcome, accessible states) and the preview page.
- **Tests:** network and route deny test (assistant cannot reach auth, token, tenant, billing routes; forged headers); secret canary suite (P0-01, P0-02); origin and overposting rejection; a stopped backend shows 503 (never a fake success).
- **Exit:** G0 evidence draft (boundary tests green); flags OFF; log scan shows no raw text.

### Phase 2: Knowledge and publication truth (B04, B05, B07, B08; Wave 2; OD-004..007)
**Goal:** the system can say "I can't confirm that" correctly, and withdraw a claim instantly.
- P01..P05 schemas and loader, snapshot compiler with digest, immutable revisions, state machine (DRAFT to PUBLISHED to WITHDRAWN).
- **Eligibility evaluator** (Doc 08 section 11): signed snapshot, then source, then capability, then market and audience, then sensitive-claim approvals. Any failure or missing snapshot means ABSTAIN.
- Withdrawal that is checked at serving time (warm cache and in-flight), click-time link validation.
- Load the 28 draft answers and candidate capabilities as `DRAFT_NOT_PUBLISHED`. **A clearly marked engineering sample snapshot** lets us test the pipeline. It can never be mistaken for approved content, and production startup refuses an unsigned snapshot.
- Postgres full-text retrieval over approved spans.
- **Tests:** unsupported or region-restricted claim abstains (P0-03); withdrawal during in-flight response (P0-04); expired source, poisoned snippet, conflict quarantine.
- **Exit:** G1 evidence draft; zero unsupported availability assertions on the golden vectors.

### Phase 3: Conversation and controlled AI (B06, B01 complete; WF-02/03, CW-01..04)
**Goal:** useful, cited answers with the model fully fenced in.
- 18-intent deterministic router, discovery flow, source-cited answer cards (typed packet; text is rendered escaped, only server-issued links become links).
- Model gateway: provider interface, pinned prompt and policy version, JSON-schema output, claim verifier, token and cost ceilings, circuit breaker, deadline. `mock` provider first; **Claude provider** behind `assistant_ai_answers` (OFF).
- **Complete verified answers only**, no token streaming (ADR-05).
- Widget completed: WF-02 conversation, WF-03 discovery, WF-04 routing, loading, offline and refusal states.
- **Tests:** prompt-injection and poisoned-snippet corpus (OWASP LLM Top 10 cases), schema tests, unsafe Markdown and XSS (P0-06), budget and timeout, fallback when the model is down (static help still works).
- **Exit:** G3 draft for the answer path; the model can never override a deny verdict.

### Phase 4: Handoff, privacy and operations (B09, B10; Wave 3; OD-009..015)
**Goal:** the optional contact path and the privacy machinery, both OFF until legal and ops decide.
- Intake (WF-10A sales, WF-10B support): config, sales and support routes, idempotency, DLP, encrypted store, **transactional outbox**, delivery with receipt that is only shown after durable acceptance, log-only adapter until a real CRM or helpdesk exists.
- Private-topic redirect to the authenticated product flows (never collected in chat). Marketing election as a separate record, OFF.
- Retention jobs (RET-01..12 as configuration, not hard-coded legal claims), deletion tombstones, feedback, session forget.
- Kill switches (chat, model, intake independently), alerts, privacy-safe metrics, runbook.
- **Tests:** unchecked consent creates no record, and an unacknowledged delivery never shows "submitted" (P0-05); duplicate and timeout replay; outage with no loss or duplication; log-scan.
- **Exit:** G2 and G4 evidence drafts. Intake stays disabled until OD-009..015 are signed.

### Phase 5: Assurance and release evidence (B11, B12; Wave 4)
**Goal:** prove it, and package the evidence for the gate owners.
- Golden corpus (18 intents x 28 answers, market and locale negatives), all P0-01..08, intake test register, accessibility pass (WCAG 2.2 AA), load and abuse runs, regional and vendor negative tests.
- Threat-model and runbook additions in the existing `docs/threat-model` and `docs/runbooks`.
- G0..G4 evidence ledger and an Open Decisions tracker mirroring Doc 07 (every OD with state, owner and evidence link). Test traceability matrix (AT-style, like the ZoikoShield one).
- **Exit:** every P0 passes; evidence filed. This is the point where humans sign, and code cannot sign for them.

### Phase 6: Website integration and rollout (after gate sign-off)
**Goal:** the same widget on `Zoiko-ID-Website`, behind a canary.
- Publish the widget package; in the website add the mount in `app/layout.tsx` and a server-side route handler that proxies to the assistant service (key and cookies stay server-side).
- CSP, CORS and cookie-scope settings; production env; deploy through the website's GitHub Action; canary, rollback rehearsal, owner handover.
- **Exit:** G4 signed; chat can be disabled without affecting the site's navigation or sign-in (INV-07).

---

## 6. Decisions only people can make (from Doc 07, mapped to phases)

| Needed by | Decision | Owner |
|---|---|---|
| Start of Phase 1 | **D1: separate service** instead of a monolith module (OD-001, ADR-02) | CTO / IAM Security |
| Phase 2 | P01 publisher, P02 sources, P03 links, P04/P05 claims (OD-004..007) | Product / Knowledge / Legal / Commercial |
| Phase 3 (real model) | Model vendor, region, data terms, budget (OD-010/016/017) | AI Platform / Privacy / Finance |
| Phase 4 | Controller map, retention, rights process (OD-009/011/012), intake fields, endpoints, routing (OD-013..015) | Privacy / Counsel / Commercial / Support |
| Phase 5 | Test basis for accessibility and security (OD-019..023) | Security / QA / UX |
| Phase 6 | Name and branding (OD-008), final production owner (OD-024) | Founder / CTO |

## 7. Risks
- **Scope drift into the monolith.** Mitigation: separate package, a test that fails if it imports `zid_platform`, and a deny-matrix test in CI.
- **Sample content mistaken for approved content.** Mitigation: production startup rejects unsigned snapshots; samples are labelled in the data and in the UI.
- **Decisions arrive late.** Mitigation: phases 1 to 3 need only D1; phases 4 to 6 degrade safely (static help, no intake) if decisions are missing.
- **Local port and process clashes** (this machine runs other projects). Mitigation: all new ports are configurable; assistant defaults to 8210.

## 8. First tickets (Phase 1, in order)
1. Record D1 and the Phase 1 decisions in `docs/phases/phase-m-public-assistant.md`.
2. Scaffold `backend/assistant/` as a uv workspace member, with settings, flags, kill switch, health routes.
3. Compose service, DB role and `public_assistant` schema, CI job.
4. OpenAPI skeleton `assistant-v1.yaml` and error contract.
5. Sessions, Origin and CSRF checks, strict schemas, quotas.
6. Secret and ID interceptor with the canary suite.
7. Deny-matrix and no-import tests.
8. Widget shell and preview route.

---

## 9. Build status against this plan (2026-11-11)

| Phase | Result | Deviation from the plan |
|---|---|---|
| 1 Foundation and secure skeleton | Done | Own SQL runner instead of an Alembic stream (keeps the service free of platform imports) |
| 2 Knowledge and publication truth | Done, plus a **runtime revocation list** (instant, shared, no deploy) | Retrieval is in-memory BM25, not Postgres full-text (the snapshot is small and already immutable) |
| 3 Conversation and controlled AI | Done; model off by default; mock provider and a tested Claude provider | No live-model run (no key, vendor decision open) |
| 4 Handoff, privacy and operations | Done; forms off until a market, notice and flag are set | Receivers are a log-only default and a signed webhook adapter; no CRM exists yet |
| 5 Assurance and release evidence | Done for what code can prove (341 backend, 27 widget, 26 proxy tests; lint, types, contract, CI wiring) | Signatures, pen-test, WCAG manual tests, drills and load runs belong to people |
| 6 Website integration | **Not done / reverted.** Built once, then removed at the owner's request; the website repo is unchanged | Needs an explicit go-ahead before the website is touched again |
