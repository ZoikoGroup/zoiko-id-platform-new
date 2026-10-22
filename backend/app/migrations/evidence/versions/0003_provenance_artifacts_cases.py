"""Immutable artifact registry, provenance graph, evidence cases, receipt retention (EVIDENCE-001
§8, §9, §18, §22; CR-011).

Revision ID: evidence_0003
Revises: evidence_0002
Create Date: 2026-10-21

- artifact: the immutable version of every artifact a decision depended on (policy release,
  action definition, ...). Registered once per (type, ref, version) with its content hash; the
  ledger commits it; replay checks the historic artifact still hashes the same (EVID-08).
- provenance_edge: W3C PROV-aligned edges (used, wasGeneratedBy, wasAssociatedWith,
  wasAttributedTo, wasDerivedFrom, wasInformedBy, actedOnBehalfOf) between immutable ids. One
  tenant per edge: cross-tenant edges cannot be written (§8.1).
- evidence_case: investigation / audit / legal cases with a bound purpose and expiry; items are
  custody records (§18).
- Receipt retention (§22): decision_receipt / audit_record / receipt_annotation rows may be
  removed only inside the retention sweep's transaction (app.evidence_retention = 'purge'). The
  ledger entry (hash, position, signature) is never removed, so history keeps verifying and the
  deletion itself is evidenced.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0003"
down_revision = "evidence_0002"
branch_labels = None
depends_on = None

DIGEST = r"'^sha-(256|384|512):[0-9a-f]+$'"
TABLES = ("artifact", "provenance_edge", "evidence_case")
APPEND_ONLY = frozenset({"artifact", "provenance_edge"})
PLATFORM_CAPABLE = frozenset({"artifact"})  # platform artifacts (action definitions) have no tenant


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.artifact (
            artifact_id         uuid PRIMARY KEY,
            tenant_id           uuid,
            artifact_type       text NOT NULL CHECK (artifact_type IN ('POLICY_RELEASE',
                                'ACTION_DEFINITION', 'SCHEMA_RELEASE', 'JURISDICTION_RULE_PACK',
                                'VERIFICATION_PROFILE', 'CREDENTIAL_TRUST_PROFILE',
                                'SIGNAL_RULE_RELEASE', 'AGENT_BLUEPRINT', 'SIGNING_MATRIX_RELEASE',
                                'CONSENT_NOTICE_VERSION')),
            artifact_ref        text NOT NULL,
            version             text NOT NULL,
            content_hash        text NOT NULL CHECK (content_hash ~ {DIGEST}),
            body                jsonb NOT NULL,
            registered_at       timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE UNIQUE INDEX artifact_identity ON evidence.artifact
            (coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), artifact_type,
             artifact_ref, version);

        CREATE TABLE evidence.provenance_edge (
            edge_id             uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            relation            text NOT NULL CHECK (relation IN ('used', 'wasGeneratedBy',
                                'wasAssociatedWith', 'wasAttributedTo', 'wasDerivedFrom',
                                'wasInformedBy', 'actedOnBehalfOf')),
            subject_kind        text NOT NULL CHECK (subject_kind IN ('entity', 'activity', 'agent')),
            subject_type        text NOT NULL,
            subject_ref         text NOT NULL,
            object_kind         text NOT NULL CHECK (object_kind IN ('entity', 'activity', 'agent')),
            object_type         text NOT NULL,
            object_ref          text NOT NULL,
            receipt_id          uuid,
            recorded_at         timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX provenance_subject ON evidence.provenance_edge (tenant_id, subject_type, subject_ref);
        CREATE INDEX provenance_object ON evidence.provenance_edge (tenant_id, object_type, object_ref);

        CREATE TABLE evidence.evidence_case (
            case_id             uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            case_kind           text NOT NULL CHECK (case_kind IN ('INVESTIGATION', 'AUDIT', 'LEGAL',
                                'INCIDENT', 'DATA_RIGHTS')),
            purpose             text NOT NULL,
            legal_basis         text,
            reference_commitment text NOT NULL CHECK (reference_commitment ~ {DIGEST}),
            opened_by_handle    text NOT NULL,
            status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
            opened_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at          timestamptz NOT NULL,
            closed_at           timestamptz,
            CHECK (expires_at > opened_at),
            CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL))
        );
    """)
    for table in TABLES:
        if table in APPEND_ONLY:
            _run(f"""
                CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON evidence.{table}
                    FOR EACH ROW EXECUTE FUNCTION evidence.refuse_mutation();
                CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON evidence.{table}
                    FOR EACH STATEMENT EXECUTE FUNCTION evidence.refuse_mutation();
            """)
        rule = (
            "((tenant_id IS NOT NULL AND tenant_id = "
            "nullif(current_setting('app.tenant_id', true), '')::uuid) OR (tenant_id IS NULL AND "
            "current_setting('app.evidence_scope', true) IN ('platform-security', 'artifacts')))"
            if table in PLATFORM_CAPABLE
            else "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
        )
        _run(f"""
            ALTER TABLE evidence.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE evidence.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON evidence.{table} USING {rule} WITH CHECK {rule};
        """)
    # Platform artifacts (tenant NULL) are readable from every tenant transaction: they are the
    # shared, non-personal catalogue every receipt refers to (still written only by the platform).
    _run("""
        CREATE POLICY platform_artifacts_read ON evidence.artifact FOR SELECT USING (tenant_id IS NULL);

        CREATE OR REPLACE FUNCTION evidence.refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'checkpoint' THEN
                IF OLD.external_timestamp_ref IS NULL AND NEW.external_timestamp_ref IS NOT NULL
                   AND (to_jsonb(NEW) - 'external_timestamp_ref')
                       = (to_jsonb(OLD) - 'external_timestamp_ref') THEN
                    RETURN NEW;
                END IF;
            END IF;
            -- Scheduled retention (EVIDENCE §22) inside the retention sweep only; the ledger entry,
            -- checkpoints and artifacts are never purged.
            IF TG_OP = 'DELETE'
               AND TG_TABLE_NAME IN ('decision_receipt', 'audit_record', 'receipt_annotation')
               AND current_setting('app.evidence_retention', true) = 'purge' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'evidence.% is append-only (% refused)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE evidence.evidence_case;
        DROP TABLE evidence.provenance_edge;
        DROP TABLE evidence.artifact;
    """)
