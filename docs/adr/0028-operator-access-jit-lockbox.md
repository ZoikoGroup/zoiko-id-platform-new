# ADR-0028: Operator access: zero standing privilege, JIT, lockbox and break-glass

- **Status:** Proposed
- **Owner:** Security Lead + SRE Lead (names to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** Back-End Target Architecture P0-07 ("no human holds standing production data access"); Security spec §11–13
- **Sources:** Security Architecture, Threat Model & Operator Trust spec §11, §12, §13; Multi-Tenant/Residency spec §19 (operator sovereignty); UI/UX Wireframes §21 (Operator Console); Data Model §12.2 (`breakglass_dba`)

## Context
Insiders, coerced operators and compromised support tooling are named adversaries. Zoiko staff must be able to operate the platform without becoming an implicit trust bypass.

## Decision

### 1. Zero standing production privilege
Operators have no routine access to customer data or unrestricted infrastructure administration. All privilege is **requested for a purpose, approved, time-bounded, scoped, attributable and auto-revoked**.

### 2. OPS service (in `platform-control`)

| Control | Implementation |
|---|---|
| Workforce identity | Dedicated workforce IdP identity, a phishing-resistant passkey, managed-device posture, and a separate privileged identity where required |
| JIT request | Case/ticket, reason, exact tenant/environment/resource, capability and duration |
| Approval | Independent approver; **two-person control** for R3, security, key and data-export operations |
| Customer lockbox | Customer approval for customer-data access where required or contracted; scoped and time-bound |
| Session brokerage | Ephemeral privileged session through a controlled proxy or bastion; no credential export, no shared credentials |
| Evidence | Every protected action is audited (E1/E2/E3 by sensitivity) with before/after commitments, without capturing customer content unnecessarily |
| Auto-revoke | Privilege expires automatically; no open-ended extension |
| Break-glass | Named emergency role, narrow scope, immediate Security notification, **mandatory post-review**, region/profile constraints still apply |

### 3. Flow
```
request (identity + device + passkey + case + scope + duration)
  → policy check: ordinary → independent approver
                  customer data → + customer lockbox
                  R3 / key / evidence admin → dual approval, separated duties
                  emergency → break-glass policy
  → ephemeral capability (audited, continuously re-checked against security state)
  → automatic expiry / explicit revoke → review + evidence receipt
```

### 4. Separation of duties
- Production admin, evidence admin, key admin and security-policy capabilities are **never concentrated** by default.
- Key control doesn't allow evidence deletion.
- Support can't create tenant roles or grants.
- Commercial status can't override trust decisions.
- Incident response can contain systems but cannot delete evidence.

### 5. Hard rules
- **Impersonating a customer user is prohibited.** Troubleshooting uses explicit support context.
- **No direct database mutation** as an operator path. Operator actions go through protected service APIs.
- `breakglass_dba` is JIT-only, with captured sessions.
- The Operator Console is a **separate origin, OIDC client and identity policy** (ADR-0013).
- SRP-2/3 tenants may restrict operator jurisdiction and location (ADR-0011).

## Acceptance
- No production role grants standing data access (verified by an access review).
- A JIT grant expires on time.
- Data access without the lockbox is denied where it's required.
- Break-glass triggers an alert and the review workflow.
- Readiness plan: protected administrator and customer-lockbox control tests pass.

## Consequences
The OPS module and `operator-console` are W1/W2 deliverables, because controlled production (G4) requires them.
