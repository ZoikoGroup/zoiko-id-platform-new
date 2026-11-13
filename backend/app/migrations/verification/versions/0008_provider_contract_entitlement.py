"""Partner entitlement statements (ZID-RCG-003 RCG-G03, sections 05.1 and 05.3; CR-074).

Revision ID: verification_0008
Revises: verification_0007
Create Date: 2026-11-12

provider_contract_entitlement: one row per statement about what Zoiko may use a provider for in one country, capability and purpose. Append-only: the latest
statement effective at a moment says what was true then, so a later suspension never rewrites a check made before it. Each statement names the
contract and the documented terms it rests on. Platform configuration, not tenant data: no tenant_id, no row-level security. The table repeats the
domain rules: no update, no delete, never from the future, and each statement for a scope newer than the one before it.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0008"
down_revision = "verification_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.provider_contract_entitlement (
            grant_id      text PRIMARY KEY CHECK (length(grant_id) BETWEEN 1 AND 128),
            provider      text NOT NULL CHECK (length(btrim(provider)) BETWEEN 1 AND 128),
            country       text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            capability    text NOT NULL CHECK (capability ~ '^[A-Z][A-Z0-9_]{0,63}$'),
            purpose       text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z0-9_]{0,63}$'),
            status        text NOT NULL CHECK (status IN ('GRANTED', 'SUSPENDED', 'WITHDRAWN')),
            effective_at  timestamptz NOT NULL,
            valid_from    timestamptz NOT NULL,
            valid_until   timestamptz,
            contract_ref  text NOT NULL CHECK (contract_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            terms_ref     text NOT NULL CHECK (terms_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            recorded_by   text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at   timestamptz NOT NULL,
            CHECK (valid_until IS NULL OR valid_until > valid_from)
        );
        CREATE INDEX provider_contract_entitlement_scope
            ON verify.provider_contract_entitlement (provider, country, capability, purpose, effective_at DESC);

        CREATE FUNCTION verify.provider_contract_entitlement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'provider entitlements are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'an entitlement statement cannot be from the future' USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM verify.provider_contract_entitlement e
                       WHERE e.provider = NEW.provider AND e.country = NEW.country AND e.capability = NEW.capability
                         AND e.purpose = NEW.purpose AND e.effective_at >= NEW.effective_at) THEN
                RAISE EXCEPTION 'an entitlement statement must be newer than the one before it for the same scope' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_contract_entitlement_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.provider_contract_entitlement
            FOR EACH ROW EXECUTE FUNCTION verify.provider_contract_entitlement_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.provider_contract_entitlement;
        DROP FUNCTION verify.provider_contract_entitlement_guard();
    """)
