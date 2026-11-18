"""Licensed provider calls made for a screening case: one row per call, the idempotency key and the bill (ZID-DTV-WF-06 S11, ENG-10, ENG-11, ENG-13; CR-113).

Revision ID: screening_0008
Revises: screening_0007
Create Date: 2026-11-18

screening_supplier_call: one row for every request made to a licensed provider for a case. The call key (tenant, case, provider, class) is the primary key, so a retry or a replay is the same call and is
never billed twice; an outage is recorded with no cost; a provider's own request and response ids are kept for reconciliation. Spend and call counts for the budget and the quota are summed from this table, so
the gate that stops a paid call before the charge reads the same facts the bill is made from. The table is append-only.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0008"
down_revision = "screening_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.screening_supplier_call (
            call_key       text PRIMARY KEY CHECK (call_key ~ '^[0-9a-f]{64}$'),
            tenant_id      uuid NOT NULL,
            request_id     uuid NOT NULL REFERENCES verify.screening_request (request_id),
            provider_id    text NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 64),
            klass          text NOT NULL CHECK (klass IN ('SANCTIONS', 'PEP_RCA', 'ADVERSE_MEDIA', 'OWNERSHIP_CONTROL')),
            region         text NOT NULL,
            contract_version text NOT NULL,
            price_version  text NOT NULL,
            outcome        text NOT NULL CHECK (outcome IN ('NO_HITS', 'HITS', 'UNAVAILABLE')),
            cost           numeric(12, 4) NOT NULL DEFAULT 0 CHECK (cost >= 0),
            billable       boolean NOT NULL DEFAULT false,
            provider_request_id  text,
            provider_response_id text,
            created_at     timestamptz NOT NULL
        );
        CREATE INDEX screening_supplier_call_usage ON verify.screening_supplier_call (tenant_id, provider_id, created_at);
        CREATE TRIGGER screening_supplier_call_append_only BEFORE UPDATE OR DELETE ON verify.screening_supplier_call
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();
        ALTER TABLE verify.screening_supplier_call ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.screening_supplier_call FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.screening_supplier_call
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("DROP TABLE verify.screening_supplier_call;")
