# Phase H: Product surfaces

> **Gate (Build Documentation Guideline §3):** Implements engineering/admin surfaces against already-defined back-end contracts.

Back to the [phase index](README.md).

## Spec documents

| # | Doc ID | Title |
|---|---|---|
| 24 | ZID-UX-001 | Product Console & Engineering UI/UX Wireframes |

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
