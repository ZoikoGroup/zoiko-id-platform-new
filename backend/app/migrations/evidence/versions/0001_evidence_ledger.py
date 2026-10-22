"""Evidence ledger: hash-chained append-only entries and signed checkpoints (EVIDENCE-001 §10-12; DEC-F2).

Revision ID: evidence_0001
Revises:
Create Date: 2026-10-21

Columns follow DEC-F2 §2 (EVIDENCE-001 §10), with these additions (CR-011):
- ledger_entry: tenant_id (RLS), record_type, entry_hash, effective_at / observed_at (DEC-F2 §4 keeps
  them separate from committed_at). The spec's `receipt_id / audit_id` is the generic
  (record_type, record_id) pair, so receipts (F-W2) and audit records share one ledger.
- checkpoint: tenant_id (RLS), checkpoint_hash (what the next checkpoint links to), alg (JWS alg) and
  signer_public_jwk (historic verification material is retained for the life of the evidence).
Every digest is text `<alg>:<hex>`: the algorithm id is stored with every digest (EVIDENCE §5.1).

Append-only is enforced in the database, not only in code: UPDATE, DELETE and TRUNCATE are refused
by triggers for every role, the table owner included (only a deliberate `ALTER TABLE ... DISABLE
TRIGGER` by the owner can bypass them, and the hash chain and signed checkpoints then expose it).
The one permitted change is filling a checkpoint's NULL external_timestamp_ref once (an RFC 3161
token obtained after a TSA outage, DEC-F2 §3). committed_at / created_at must be the database's own
clock at insert time (DEC-F2 §4).

Partitions: `tenant:<tenant_id>:<cell>` rows carry their tenant_id and are isolated by the usual
tenant RLS policy. The platform-security partition (`platform-security:<cell>`) has tenant_id NULL;
its rows are visible only inside a transaction that set `app.evidence_scope = 'platform-security'`
(set by the evidence module's platform-scope helpers, never by a tenant request path), so no tenant
context can read or write them.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("ledger_entry", "checkpoint")

DIGEST = r"'^sha-(256|384|512):[0-9a-f]+$'"
PARTITION_MATCHES_TENANT = (
    "((tenant_id IS NULL AND partition_id ~ '^platform-security:[a-z0-9-]+$') OR "
    "(tenant_id IS NOT NULL AND partition_id = 'tenant:' || tenant_id::text || ':' || "
    "split_part(partition_id, ':', 3) AND split_part(partition_id, ':', 3) ~ '^[a-z0-9-]+$'))"
)


def upgrade() -> None:
    _run(f"""
        CREATE TYPE evidence.evidence_class AS ENUM ('E0', 'E1', 'E2', 'E3');

        CREATE TABLE evidence.ledger_entry (
            partition_id        text NOT NULL,
            sequence_no         bigint NOT NULL CHECK (sequence_no >= 1),
            tenant_id           uuid,
            record_type         text NOT NULL CHECK (record_type ~ '^[a-z][a-z0-9_.]{{0,63}}$'),
            record_id           text NOT NULL CHECK (length(record_id) BETWEEN 1 AND 200),
            record_hash         text NOT NULL CHECK (record_hash ~ {DIGEST}),
            previous_entry_hash text CHECK (previous_entry_hash ~ {DIGEST}),
            entry_hash          text NOT NULL CHECK (entry_hash ~ {DIGEST}),
            evidence_class      evidence.evidence_class NOT NULL,
            committed_at        timestamptz NOT NULL,
            effective_at        timestamptz,
            observed_at         timestamptz,
            signer_key_id       text,
            entry_signature     text,
            PRIMARY KEY (partition_id, sequence_no),
            UNIQUE (partition_id, record_type, record_id),
            CHECK ((sequence_no = 1) = (previous_entry_hash IS NULL)),
            CHECK ((entry_signature IS NULL) OR (signer_key_id IS NOT NULL)),
            CHECK {PARTITION_MATCHES_TENANT}
        );
        CREATE INDEX ledger_entry_record ON evidence.ledger_entry (record_id);
        CREATE INDEX ledger_entry_tenant ON evidence.ledger_entry (tenant_id, partition_id);

        CREATE TABLE evidence.checkpoint (
            checkpoint_id            uuid PRIMARY KEY,
            partition_id             text NOT NULL,
            tenant_id                uuid,
            first_sequence           bigint NOT NULL CHECK (first_sequence >= 1),
            last_sequence            bigint NOT NULL,
            merkle_root              text NOT NULL CHECK (merkle_root ~ {DIGEST}),
            previous_checkpoint_hash text CHECK (previous_checkpoint_hash ~ {DIGEST}),
            checkpoint_hash          text NOT NULL CHECK (checkpoint_hash ~ {DIGEST}),
            created_at               timestamptz NOT NULL,
            signer_key_id            text NOT NULL,
            alg                      text NOT NULL,
            checkpoint_signature     text NOT NULL,
            signer_public_jwk        jsonb NOT NULL,
            external_timestamp_ref   text,
            CHECK (last_sequence >= first_sequence),
            CHECK ((first_sequence = 1) = (previous_checkpoint_hash IS NULL)),
            UNIQUE (partition_id, first_sequence),
            UNIQUE (partition_id, last_sequence),
            CHECK {PARTITION_MATCHES_TENANT}
        );
        CREATE INDEX checkpoint_tenant ON evidence.checkpoint (tenant_id, partition_id);

        CREATE FUNCTION evidence.refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'checkpoint' THEN
                IF OLD.external_timestamp_ref IS NULL AND NEW.external_timestamp_ref IS NOT NULL
                   AND (to_jsonb(NEW) - 'external_timestamp_ref')
                       = (to_jsonb(OLD) - 'external_timestamp_ref') THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'evidence.% is append-only (% refused)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;

        CREATE FUNCTION evidence.require_db_time() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            t timestamptz := (to_jsonb(NEW) ->> CASE TG_TABLE_NAME
                WHEN 'ledger_entry' THEN 'committed_at' ELSE 'created_at' END)::timestamptz;
        BEGIN
            IF t > clock_timestamp() OR t < clock_timestamp() - interval '5 minutes' THEN
                RAISE EXCEPTION 'evidence.% time must be the database clock at insert', TG_TABLE_NAME
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    for table in TABLES:
        _run(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON evidence.{table}
                FOR EACH ROW EXECUTE FUNCTION evidence.refuse_mutation();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON evidence.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION evidence.refuse_mutation();
            CREATE TRIGGER {table}_db_time BEFORE INSERT ON evidence.{table}
                FOR EACH ROW EXECUTE FUNCTION evidence.require_db_time();
            ALTER TABLE evidence.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE evidence.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON evidence.{table}
                USING (
                    (tenant_id IS NOT NULL
                     AND tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                    OR (tenant_id IS NULL
                        AND current_setting('app.evidence_scope', true) = 'platform-security'))
                WITH CHECK (
                    (tenant_id IS NOT NULL
                     AND tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                    OR (tenant_id IS NULL
                        AND current_setting('app.evidence_scope', true) = 'platform-security'));
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE evidence.checkpoint;
        DROP TABLE evidence.ledger_entry;
        DROP FUNCTION evidence.require_db_time();
        DROP FUNCTION evidence.refuse_mutation();
        DROP TYPE evidence.evidence_class;
    """)
