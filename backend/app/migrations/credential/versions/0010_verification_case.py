"""Verification cases: one case, several separate checks (ZID-INT-002 sections 04 and 05.1; CR-071).

Revision ID: credential_0010
Revises: credential_0009
Create Date: 2026-11-11

A case groups the checks one person is asked to complete (identity, degree, license, insurance ...). Each check is its OWN task with its
OWN outcome: the case never collapses them into one pass. The case holds references only (the check id that holds the signed
assertion, the identity verification session id), never a claimed value or a document.

Both tables hold a person's reference, so governed erasure covers them: `cred.erase_subject` is replaced here to delete the case tasks
and cases as well (it stays the only way past the immutability guards of the other credential tables).
"""

from zid_platform.migrations import execute_script as _run

revision = "credential_0010"
down_revision = "credential_0009"
branch_labels = None
depends_on = None

TENANT_TABLES = ("verification_case", "verification_case_task")

ERASE_SQL = """
        CREATE OR REPLACE FUNCTION cred.erase_subject(p_tenant uuid, p_subject uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = cred, pg_temp AS $$
        DECLARE
            n integer := 0;
            c integer;
        BEGIN
            IF p_tenant IS DISTINCT FROM nullif(current_setting('app.tenant_id', true), '')::uuid THEN
                RAISE EXCEPTION 'erasure is for the current tenant only' USING ERRCODE = '42501';
            END IF;
            PERFORM set_config('cred.erasing', 'on', true);
            DELETE FROM cred.verification_case_task
                WHERE case_id IN (SELECT case_id FROM cred.verification_case WHERE subject_principal_id = p_subject);
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
            DELETE FROM cred.verification_case WHERE subject_principal_id = p_subject;
            GET DIAGNOSTICS c = ROW_COUNT; n := n + c;
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
    _run("""
        CREATE TABLE cred.verification_case (
            case_id               uuid PRIMARY KEY,
            tenant_id             uuid NOT NULL,
            subject_principal_id  uuid NOT NULL,
            purpose               text NOT NULL,
            country               text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            subdivision           text,
            profile_id            text NOT NULL,
            profile_version       text NOT NULL,
            consent_ref           text,
            callback_endpoint_id  uuid,
            client_ref            text,
            policy_packs          jsonb NOT NULL DEFAULT '[]'::jsonb,
            state                 text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'COMPLETE', 'CANCELED', 'EXPIRED')),
            created_by            uuid NOT NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            expires_at            timestamptz NOT NULL,
            closed_at             timestamptz,
            CHECK (expires_at > created_at),
            CHECK ((state = 'PENDING') = (closed_at IS NULL))
        );
        CREATE INDEX verification_case_subject ON cred.verification_case (tenant_id, subject_principal_id, created_at DESC);

        CREATE TABLE cred.verification_case_task (
            task_id                   uuid PRIMARY KEY,
            case_id                   uuid NOT NULL REFERENCES cred.verification_case (case_id),
            tenant_id                 uuid NOT NULL,
            check_kind                text NOT NULL CHECK (check_kind IN (
                'identity', 'degree', 'license', 'insurance', 'employment', 'cpd', 'certification')),
            state                     text NOT NULL DEFAULT 'PENDING_INPUT' CHECK (state IN ('PENDING_INPUT', 'IN_PROGRESS', 'COMPLETE')),
            credential_check_id       uuid,
            identity_verification_id  uuid,
            outcome                   text,
            updated_at                timestamptz NOT NULL DEFAULT now(),
            UNIQUE (case_id, check_kind),
            CHECK ((state = 'COMPLETE') = (outcome IS NOT NULL))
        );
    """)
    for table in TENANT_TABLES:
        _run(f"""
            ALTER TABLE cred.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE cred.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON cred.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)
    _run(
        ERASE_SQL
    )  # a function that deletes when CALLED is runtime behavior; upgrade() itself only adds and replaces


def downgrade() -> None:
    _run("""
        CREATE OR REPLACE FUNCTION cred.erase_subject(p_tenant uuid, p_subject uuid) RETURNS integer
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
        DROP TABLE cred.verification_case_task;
        DROP TABLE cred.verification_case;
    """)
