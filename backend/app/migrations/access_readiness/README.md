# Migrations: `access_readiness` module

Alembic stream owned by the **access_readiness** module (zoiko-id-platform, DEC-D6; CR-091). Version table: `zid_migrations.alembic_version__access_readiness`.

May create or alter **only** these tables (enforced by CI against `backend/database/table-ownership.yaml`):

- `agent.access_review_setup`
- `agent.access_review_approval`
- `agent.access_review_run`
- `agent.access_review_policy_decision`
- `agent.access_review_evidence`
- `agent.access_review_proposal`
- `agent.access_review_handoff`

Run: `python -m zid_platform.migrations upgrade --url ... --stream access_readiness`
