# Interface registry

One entry per API route, RPC, event and projection (ZID-SVC-001 §22; ZID-API-001 route registry: *no production route is complete until it is present in the route registry*).

Required fields per entry: `service_id`, `interface_id`, `type` (COMMAND | QUERY | DECISION | EVENT | PROJECTION), `version`, `owner`, `data_class` (DC0-DC3), `tenant_scope`, `auth_profile`, `idempotency`, `consistency` (STRONG | BOUNDED | EVENTUAL), `slo`, `dependencies`, `evidence_class` (E0-E3), `deprecation`.

CI fails when a route exists in code or OpenAPI without an entry here, or when declared dependencies violate the acyclic dependency rules (architecture §6.1).
