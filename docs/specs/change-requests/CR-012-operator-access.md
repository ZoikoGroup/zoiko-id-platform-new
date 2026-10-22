# CR-012: Operator access (JIT, customer lockbox, break-glass)

- **Target:** ZID-SEC-001 §11-13; ZID-TENANCY-001 §19; ZID-DATA-001 (new tables); event catalogue; action catalogue · **Raised by:** Phase F W4 (DEC-F4) · **Status:** Open

## Tables (`platform` schema, owner: operator_access)

`operator_profile`, `jit_grant`, `operator_access_approval` (append-only), `lockbox_request`, `operator_access_policy`, `break_glass_review`. Operator-plane rows live in the dedicated operator tenant (`ZID_OPERATOR_TENANT_ID`); lockbox requests and the tenant's operator-access policy live in the customer tenant. Database guards: an ACTIVE grant never outlives its approved duration, nobody approves their own request or reviews their own break-glass.

## Actions

| Action | Floor | Use |
|---|---|---|
| `operator.lockbox.approve` | R3/E3 | customer administrator decides a lockbox request (approve, deny, withdraw) |
| `operator.access_policy.manage` | R3/E3 | tenant operator-access policy (lockbox required, operator jurisdictions) |

`operator.jit.elevate` (already catalogued) names the capability class; operator endpoints are gated by the operator OIDC client + operator tenant + AAL-Z3, not by tenant authority.

## Events (proposed-events.yaml)

`operator_access.requested`, `.approval.recorded`, `.approved`, `.denied`, `.cancelled`, `.activated`, `.revoked`, `.expired`, `.break_glass.activated`, `.review.completed`, `.lockbox.requested`, `.lockbox.approved`, `.lockbox.denied`, `.lockbox.revoked`, `.policy.changed`, `.operator.registered`. Payloads carry ids, classes and SHA-256 commitments only (no case text or customer content). Each also writes a protected AuditRecord: operator-plane changes to the platform-security partition, lockbox and policy changes to the customer's own partition.

## Spec alignment

- Environment and cell of a grant are the serving cell's, never request input (INV-13).
- SRP-2/3 tenants restrict operator jurisdictions through their residency record even when their own policy is empty (CR-014).
- Session brokerage (ephemeral bastion/proxy sessions) is infrastructure (Phase G) and is not part of this change.
