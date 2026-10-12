// Platform-floor policies (ZID-AUTHZ-001 §0.2, §20; DEC-B1; DEC-C3).
// Evaluated together with every tenant release; tenants can add permits and forbids but can never
// remove these, and in Cedar a matching forbid always beats any permit.
// The PDP fills missing context with deny-safe defaults (authority_stale = true,
// security_state = "UNKNOWN", tenant_match = false), so unknown state never becomes an allow.

// Tenant isolation: the request must stay inside the server-derived tenant.
@id("floor-tenant-isolation")
forbid (principal, action, resource)
unless { context.tenant_match };

// Compromised security state denies everything.
@id("floor-security-compromised")
forbid (principal, action, resource)
when { context.security_state == "COMPROMISED" };

// R2/R3 fail closed when security state or authority freshness can't be established.
@id("floor-r2-r3-unknown-security-state")
forbid (principal, action, resource)
when { context.risk_class >= 2 && context.security_state == "UNKNOWN" };

@id("floor-authority-stale")
forbid (principal, action, resource)
when { context.risk_class >= 2 && context.authority_stale };
