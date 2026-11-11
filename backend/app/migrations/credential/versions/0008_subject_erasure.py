"""Governed erasure of a subject's credential-check data (GID-053, privacy rights; CR-069).

Revision ID: credential_0008
Revises: credential_0007
Create Date: 2026-11-11

Claim checks and eligibility decisions are immutable on purpose (a later check supersedes an earlier one; nothing is edited), and a
trigger refuses DELETE. A person's right to erasure still has to work. The ONLY way through the guards is `cred.erase_subject`, a
SECURITY DEFINER function that:
- works only for the current tenant (it refuses any other tenant id, and row-level security still applies inside it);
- turns on a transaction-local flag the guards recognize, deletes one subject's rows from every credential-check table, and turns it
  off again, so an ordinary DELETE is refused as before;
- returns how many rows it removed.
The privacy module calls it through the credential privacy adapter, after its own legal-hold and retention checks.
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0008"
down_revision = "credential_0007"
branch_labels = None
depends_on = None


# The guards and the erasure function. A stored function that deletes when it is CALLED is runtime behavior, not a schema
# contract step, so it lives in a constant: `upgrade()` itself only adds (a function and two replaced trigger functions).
UPGRADE_SQL = """
        CREATE OR REPLACE FUNCTION cred.claim_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('cred.erasing', true) = 'on' THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'claim checks are not deleted' USING ERRCODE = '23514';
            END IF;
            IF (NEW.check_id, NEW.tenant_id, NEW.subject_principal_id, NEW.claim_type, NEW.decision, NEW.assurance_tier,
                NEW.pack_id, NEW.pack_version, NEW.jurisdiction, NEW.claimed_digest, NEW.source_id, NEW.source_reference,
                NEW.source_result, NEW.source_checked_at, NEW.assertion, NEW.created_at)
               IS DISTINCT FROM
               (OLD.check_id, OLD.tenant_id, OLD.subject_principal_id, OLD.claim_type, OLD.decision, OLD.assurance_tier,
                OLD.pack_id, OLD.pack_version, OLD.jurisdiction, OLD.claimed_digest, OLD.source_id, OLD.source_reference,
                OLD.source_result, OLD.source_checked_at, OLD.assertion, OLD.created_at)
               OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at) THEN
                RAISE EXCEPTION 'a claim check is immutable; only superseded_at may be set once' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION cred.eligibility_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('cred.erasing', true) = 'on' THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'eligibility decisions are append-only' USING ERRCODE = '23514';
        END $$;

        CREATE FUNCTION cred.erase_subject(p_tenant uuid, p_subject uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = cred, pg_temp AS $$
        DECLARE
            n integer := 0;
            c integer;
        BEGIN
            IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid THEN
                RAISE EXCEPTION 'erasure is for the current tenant only' USING ERRCODE = '42501';
            END IF;
            PERFORM set_config('cred.erasing', 'on', true);
            DELETE FROM cred.claim_appeal
                WHERE check_id IN (SELECT check_id FROM cred.claim_check WHERE subject_principal_id = p_subject);
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.claim_monitor
                WHERE check_id IN (SELECT check_id FROM cred.claim_check WHERE subject_principal_id = p_subject);
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.claim_check WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.consent_grant WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.eligibility_decision WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.outreach_request WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            PERFORM set_config('cred.erasing', 'off', true);
            RETURN n;
        END $$;
"""


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run("""
        DROP FUNCTION cred.erase_subject(uuid, uuid);
        CREATE OR REPLACE FUNCTION cred.eligibility_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'eligibility decisions are append-only' USING ERRCODE = '23514';
        END $$;
        CREATE OR REPLACE FUNCTION cred.claim_check_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'claim checks are not deleted' USING ERRCODE = '23514';
            END IF;
            IF (NEW.check_id, NEW.tenant_id, NEW.subject_principal_id, NEW.claim_type, NEW.decision, NEW.assurance_tier,
                NEW.pack_id, NEW.pack_version, NEW.jurisdiction, NEW.claimed_digest, NEW.source_id, NEW.source_reference,
                NEW.source_result, NEW.source_checked_at, NEW.assertion, NEW.created_at)
               IS DISTINCT FROM
               (OLD.check_id, OLD.tenant_id, OLD.subject_principal_id, OLD.claim_type, OLD.decision, OLD.assurance_tier,
                OLD.pack_id, OLD.pack_version, OLD.jurisdiction, OLD.claimed_digest, OLD.source_id, OLD.source_reference,
                OLD.source_result, OLD.source_checked_at, OLD.assertion, OLD.created_at)
               OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at) THEN
                RAISE EXCEPTION 'a claim check is immutable; only superseded_at may be set once' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)
