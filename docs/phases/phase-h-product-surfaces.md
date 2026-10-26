# Phase H: Product surfaces

> **Gate (Build Documentation Guideline §3):** Implements engineering/admin surfaces against already-defined back-end contracts.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 24 | ZID-UX-001 | Product Console & Engineering UI/UX Wireframes |

## Deliverables and status

Waves follow ZID-UX-001 §34. UX-W0 to UX-W6 and UX-W8 were delivered with Phases E to G (Tenant Console, Trust Center, Operator Console). The slices below close the gaps found against the §31 page inventory.

| ID | Deliverable | Implements | Where | Status |
|---|---|---|---|---|
| H-1 | **Developer Console: webhooks.** Management API (event types, public signing keys, endpoint register / verify / pause / resume / retire, delivery log, replay); consumer routing of allowlisted events; worker delivery; console pages for endpoints, delivery log and signature verification guide | UX-001 §18; EVENTS-001 §14-20; CR-018 | `modules/webhook`, `entrypoints/{consumer,worker}.py`, `frontend/apps/web/src/features/admin/developers` | Built: `test_webhook_api_pg.py`, `test_webhooks_pg.py`; typecheck and lint of the web app |
| H-2 | **Alerts, tasks and notification centre; global search.** Alerts derived from catalogued tenant events by fixed templates (CRITICAL / HIGH / MEDIUM / INFO), repeats collapsed, acknowledge kept apart from resolve, mandatory notices that cannot be muted; shell bell, nav badge and persistent critical banner; authorization-aware, tenant-scoped search in the command palette | UX-001 §3.1, §20, §25; CR-019, CR-020 | `modules/notification`, `modules/search`, `entrypoints/consumer.py`, `frontend/apps/web/src/features/alerts` | Built: `test_alerts_pg.py`, `test_search_pg.py` |
| H-3 | **People & principals, Help & support.** Principal detail gains Authority (grants held, mandates executed, wildcard marked) and Credentials (cryptographic validity kept apart from authority); Help & support page | UX-001 §6, §3 | `features/admin/accounts`, `features/help` | Built (typecheck, lint) |
| H-4 | **UI acceptance evidence.** Browser-free checks for colour independence, table semantics and escaping; traceability rows UX-01, 03, 19, 20, 23, 26 now point at tests | UX-001 §23, §33 | `frontend/packages/ui/test/a11y.test.tsx`, `docs/traceability/acceptance-tests.csv` | Partly: 17 UI tests. Keyboard, zoom, responsive and end-to-end flows (UX-18, 21, 22, 24) need a browser suite |

### Not built in Phase H, and why

| Spec area | Reason |
|---|---|
| Agents, tools, blueprints (§13, UX-11/12) | The `agent.agent_profile`, `tool_capability` and `blueprint_release` tables exist but have no API, and Document 09 has no owning phase. Needs a backend design decision first. |
| Settings: authentication defaults, notification subscriptions, branding, cryptography (§19) | No backend contract yet; notification channels (email, chat) and branding anti-spoofing rules are product decisions. Residency, domains, lockbox defaults, lifecycle and offboarding already exist. |
| Developer Console: API usage logs and rate limits (§18) | The `metering` module is an empty stub; the delivery log covers webhooks. |
| Optimistic concurrency on edits (UX-06, §28), saved views (§25), long-operation state (UX-29), localization (UX-31) | Cross-cutting; need If-Match / version support on the owning APIs and an i18n layer. |

## Decisions

| ID | Decision | Owner | Status |
|---|---|---|---|
| [DEC-H1](#dec-h1-nextjs-frontends-with-a-thin-backend-for-frontend) | Next.js frontends with a thin backend-for-frontend | Frontend Lead | Proposed |

---

### DEC-H1: Next.js frontends with a thin backend-for-frontend

- **Status:** Proposed
- **Owner:** Frontend Lead (name to be recorded at ratification)
- **Date:** 2026-10-10

#### Context
The UI/UX spec defines the Tenant Console, Developer Console, End-User Trust Center and a separate Zoiko Operator Console, plus hosted login. It requires that "backend authorization remains authoritative" and WCAG 2.2 AA.

#### Decision
- **Next.js (App Router) + TypeScript** in a pnpm/Turborepo workspace under `frontend/`: `frontend/apps/{tenant-console,developer-console,trust-center,hosted-login,operator-console}`, with shared `frontend/packages/{ui,api-client,auth-bff,config}`.
- UI stack: React Aria components, Tailwind CSS, TanStack Query, React Hook Form + Zod, next-intl.
- The API client is generated from FastAPI's OpenAPI spec.
- **Thin BFF:** an httpOnly Secure SameSite session cookie maps to a server-side session in cell-local Valkey. Route handlers only attach tokens and forward calls.
- **Prohibited in Next.js:** authorization decisions, business logic, direct database access and security checks in middleware.
- The Operator Console uses a separate domain, OIDC client, build and network path.
- Nonce-based CSP, HSTS and `frame-ancestors 'none'`.

#### Consequences
- Every protected mutation is re-authorized by the backend, so a UI bug can't grant access.
- The Next.js server is a small, low-privilege component.
