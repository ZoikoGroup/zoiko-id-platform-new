# Frontend

All Zoiko iD user interfaces: Next.js (App Router) + TypeScript, one pnpm + Turborepo workspace.

```
frontend/
├── package.json · pnpm-workspace.yaml · turbo.json
├── apps/
│   ├── tenant-console/      customer admins, security, compliance, auditors
│   ├── developer-console/   customer developers
│   ├── trust-center/        end users / representatives
│   ├── hosted-login/        login, step-up, consent, approval, recovery UI
│   └── operator-console/    Zoiko staff only (separate origin and identity policy)
└── packages/
    ├── ui/                  React Aria + Tailwind design system (WCAG 2.2 AA)
    ├── api-client/          generated from ../contracts/openapi
    ├── auth-bff/            session cookie, token forwarding, CSP helpers
    └── config/              shared eslint, tsconfig, tailwind presets
```

The frontend only shows screens and forwards calls. It never makes authorization decisions, holds business logic or touches a database (ADR-0013).

Full rules: [docs/architecture/repository-structure.md](../docs/architecture/repository-structure.md).
