"""Receipts, protected audit records, subject-evidence vault, legal holds (DEC-F1; EVIDENCE-001
§3, §7, §13-14, §18, §20, §22; ZID-DATA-001 §9.2-9.3; CR-011).

Revision ID: evidence_0002
Revises: evidence_0001
Create Date: 2026-10-21

Immutable evidence (append-only, refused UPDATE/DELETE/TRUNCATE by trigger, like the ledger):
- decision_receipt   pseudonymous DecisionReceipt (§3); `body` is exactly the JCS-hashed record
- audit_record       protected AuditRecord (§7); tenant rows, or platform-security rows (tenant NULL)
- receipt_annotation correction / supersession / annotation / invalidation (§20): never an edit
- custody_record     chain of custody for exports and case access (§18)
- erasure_tombstone  what was erased and when, replayed after any backup restore (PRIV-001 §10)
The ledger (evidence_0001) commits every one of these by (record_type, record_id).

Erasable, separately governed state (the only mutable evidence tables, DEC-F1 §2):
- subject_key        per-subject data key, stored only wrapped under the cell's
                     SUBJECT_ERASURE_KEY; erasure removes the wrapped key (crypto-erasure)
- subject_handle_map identity resolver: handle -> principal id, encrypted under the subject key
- evidence_object    encrypted supporting artifacts (§14), under the subject key when they are
                     about one person; content goes, the digest stays
- legal_hold         explicit, scoped, approved, time-bound holds (§22; PRIV-001 §11)
- decision_aggregate E0 counts (no subject-level receipt, §1)

Spec deviations (CR-011): handles are keyed-HMAC text (`ph1.<v>.<mac>`), not uuid, so they can be
recomputed under the key without a lookup table; policy_release_id / revisions are nullable because
a decision denied for lack of a release or an unavailable fact must still leave a receipt.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0002"
down_revision = "evidence_0001"
branch_labels = None
depends_on = None

DIGEST = r"'^sha-(256|384|512):[0-9a-f]+$'"
HANDLE = r"'^ph1\.[0-9]+\.[A-Za-z0-9_-]+$'"
TABLES = (
    "decision_receipt",
    "audit_record",
    "receipt_annotation",
    "custody_record",
    "erasure_tombstone",
    "subject_key",
    "subject_handle_map",
    "evidence_object",
    "legal_hold",
    "decision_aggregate",
)
IMMUTABLE = frozenset(
    {"decision_receipt", "audit_record", "receipt_annotation", "custody_record", "erasure_tombstone"}
)
PLATFORM_CAPABLE = frozenset({"audit_record", "custody_record"})  # may hold platform rows


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.decision_receipt (
            receipt_id                 uuid PRIMARY KEY,
            tenant_id                  uuid NOT NULL,
            decision_id                uuid NOT NULL,
            evidence_class             evidence.evidence_class NOT NULL
                                       CHECK (evidence_class <> 'E0'),
            decision_type              text NOT NULL CHECK (decision_type IN ('AUTHORIZATION',
                                       'VERIFICATION', 'AUTHORITY', 'MANDATE', 'CREDENTIAL',
                                       'RECOVERY', 'ADMIN', 'OTHER')),
            outcome                    text NOT NULL,
            reason_codes               text[] NOT NULL,
            actor_handle               text NOT NULL CHECK (actor_handle ~ {HANDLE}),
            represented_handle         text CHECK (represented_handle ~ {HANDLE}),
            action                     text NOT NULL,
            resource_class             text NOT NULL,
            purpose_id                 text,
            policy_release_id          uuid,
            relation_revision          bigint,
            security_revision          bigint,
            risk_class                 text NOT NULL CHECK (risk_class IN ('R0','R1','R2','R3')),
            input_commitment           text CHECK (input_commitment ~ {DIGEST}),
            material_object_id         uuid,
            previous_receipt_id        uuid,
            retention_class            text NOT NULL,
            body                       jsonb NOT NULL,
            receipt_hash               text NOT NULL CHECK (receipt_hash ~ {DIGEST}),
            effective_at               timestamptz NOT NULL,
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX decision_receipt_decision ON evidence.decision_receipt (tenant_id, decision_id);
        CREATE INDEX decision_receipt_recent ON evidence.decision_receipt (tenant_id, created_at DESC);
        CREATE INDEX decision_receipt_actor ON evidence.decision_receipt (tenant_id, actor_handle);

        CREATE TABLE evidence.audit_record (
            audit_id                   uuid PRIMARY KEY,
            tenant_id                  uuid,
            event_type                 text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_.]{{0,127}}$'),
            actor_type                 text NOT NULL CHECK (actor_type IN ('HUMAN', 'SERVICE',
                                       'AGENT', 'OPERATOR', 'SYSTEM')),
            actor_handle               text CHECK (actor_handle ~ {HANDLE}),
            target_type                text NOT NULL,
            target_ref                 text NOT NULL,
            action                     text NOT NULL,
            outcome                    text NOT NULL CHECK (outcome IN ('SUCCESS', 'FAILURE',
                                       'DENIED', 'PARTIAL')),
            reason_code                text,
            evidence_class             evidence.evidence_class NOT NULL
                                       CHECK (evidence_class <> 'E0'),
            source_event_id            uuid UNIQUE,
            body                       jsonb NOT NULL,
            record_hash                text NOT NULL CHECK (record_hash ~ {DIGEST}),
            occurred_at                timestamptz NOT NULL,
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX audit_record_recent ON evidence.audit_record (tenant_id, created_at DESC);
        CREATE INDEX audit_record_type ON evidence.audit_record (tenant_id, event_type, created_at DESC);

        CREATE TABLE evidence.receipt_annotation (
            annotation_id              uuid PRIMARY KEY,
            tenant_id                  uuid NOT NULL,
            receipt_id                 uuid NOT NULL REFERENCES evidence.decision_receipt,
            kind                       text NOT NULL CHECK (kind IN ('CORRECTION', 'SUPERSESSION',
                                       'ANNOTATION', 'INVALIDATION')),
            body                       jsonb NOT NULL,
            record_hash                text NOT NULL CHECK (record_hash ~ {DIGEST}),
            author_handle              text NOT NULL CHECK (author_handle ~ {HANDLE}),
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX receipt_annotation_receipt ON evidence.receipt_annotation (tenant_id, receipt_id);

        CREATE TABLE evidence.custody_record (
            custody_id                 uuid PRIMARY KEY,
            tenant_id                  uuid,
            case_ref                   text NOT NULL,
            evidence_item_id           text NOT NULL,
            custodian_handle           text NOT NULL CHECK (custodian_handle ~ {HANDLE}),
            custody_action             text NOT NULL CHECK (custody_action IN ('COLLECTED',
                                       'ACCESSED', 'EXPORTED', 'TRANSFERRED', 'SEALED', 'DESTROYED')),
            purpose                    text NOT NULL,
            export_mode                text,
            package_hash               text CHECK (package_hash ~ {DIGEST}),
            body                       jsonb NOT NULL,
            record_hash                text NOT NULL CHECK (record_hash ~ {DIGEST}),
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp()
        );

        CREATE TABLE evidence.erasure_tombstone (
            tombstone_id               uuid PRIMARY KEY,
            tenant_id                  uuid NOT NULL,
            subject_ref                text NOT NULL CHECK (subject_ref ~ {HANDLE}),
            request_ref                text NOT NULL,
            outcome                    text NOT NULL CHECK (outcome IN ('ERASED', 'PARTIAL')),
            body                       jsonb NOT NULL,
            record_hash                text NOT NULL CHECK (record_hash ~ {DIGEST}),
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX erasure_tombstone_subject ON evidence.erasure_tombstone (tenant_id, subject_ref);

        CREATE TABLE evidence.subject_key (
            tenant_id                  uuid NOT NULL,
            subject_ref                text NOT NULL CHECK (subject_ref ~ {HANDLE}),
            key_id                     uuid NOT NULL UNIQUE,
            wrapped_key                bytea,
            kek_id                     text NOT NULL,
            kek_version                integer NOT NULL,
            state                      text NOT NULL DEFAULT 'ACTIVE'
                                       CHECK (state IN ('ACTIVE', 'DESTROYED')),
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp(),
            destroyed_at               timestamptz,
            PRIMARY KEY (tenant_id, subject_ref),
            CHECK ((state = 'ACTIVE') = (wrapped_key IS NOT NULL)),
            CHECK ((state = 'DESTROYED') = (destroyed_at IS NOT NULL))
        );

        CREATE TABLE evidence.subject_handle_map (
            tenant_id                  uuid NOT NULL,
            handle                     text NOT NULL CHECK (handle ~ {HANDLE}),
            domain                     text NOT NULL,
            subject_ref                text NOT NULL CHECK (subject_ref ~ {HANDLE}),
            principal_ciphertext       bytea,
            status                     text NOT NULL DEFAULT 'ACTIVE'
                                       CHECK (status IN ('ACTIVE', 'ERASED')),
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp(),
            erased_at                  timestamptz,
            PRIMARY KEY (tenant_id, handle),
            CHECK ((status = 'ACTIVE') = (principal_ciphertext IS NOT NULL))
        );
        CREATE INDEX subject_handle_map_subject ON evidence.subject_handle_map (tenant_id, subject_ref);

        CREATE TABLE evidence.evidence_object (
            evidence_object_id         uuid PRIMARY KEY,
            tenant_id                  uuid NOT NULL,
            subject_ref                text CHECK (subject_ref ~ {HANDLE}),
            object_type                text NOT NULL CHECK (object_type ~ '^[a-z][a-z0-9_.]{{0,63}}$'),
            classification             text NOT NULL CHECK (classification IN ('D1','D2','D3','D4')),
            retention_class            text NOT NULL,
            content_hash               text NOT NULL CHECK (content_hash ~ {DIGEST}),
            object_uri                 text NOT NULL,
            encryption_key_ref         text NOT NULL,
            ciphertext                 bytea,
            status                     text NOT NULL DEFAULT 'STORED'
                                       CHECK (status IN ('STORED', 'ERASED', 'EXPIRED')),
            expires_at                 timestamptz,
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp(),
            disposed_at                timestamptz,
            CHECK ((status = 'STORED') = (ciphertext IS NOT NULL))
        );
        CREATE INDEX evidence_object_subject ON evidence.evidence_object (tenant_id, subject_ref);
        CREATE INDEX evidence_object_due ON evidence.evidence_object (expires_at)
            WHERE status = 'STORED';

        CREATE TABLE evidence.legal_hold (
            legal_hold_id              uuid PRIMARY KEY,
            tenant_id                  uuid NOT NULL,
            basis                      text NOT NULL,
            reference                  text NOT NULL,
            requested_by               uuid NOT NULL,
            approved_by                uuid NOT NULL,
            subject_refs               text[] NOT NULL DEFAULT '{{}}',
            object_types               text[] NOT NULL DEFAULT '{{}}',
            effective_at               timestamptz NOT NULL,
            review_at                  timestamptz NOT NULL,
            expires_at                 timestamptz,
            status                     text NOT NULL DEFAULT 'ACTIVE'
                                       CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
            released_at                timestamptz,
            released_by                uuid,
            release_reason             text,
            version                    integer NOT NULL DEFAULT 1,
            created_at                 timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (approved_by <> requested_by),
            CHECK (cardinality(subject_refs) > 0 OR cardinality(object_types) > 0),
            CHECK (review_at > effective_at),
            CHECK (expires_at IS NULL OR expires_at > effective_at),
            CHECK ((status = 'RELEASED') = (released_at IS NOT NULL))
        );

        CREATE TABLE evidence.decision_aggregate (
            tenant_id                  uuid NOT NULL,
            bucket                     date NOT NULL,
            action                     text NOT NULL,
            outcome                    text NOT NULL,
            decisions                  bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, bucket, action, outcome)
        );
    """)
    for table in TABLES:
        if table not in IMMUTABLE:
            continue
        _run(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON evidence.{table}
                FOR EACH ROW EXECUTE FUNCTION evidence.refuse_mutation();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON evidence.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION evidence.refuse_mutation();
        """)
    for table in TABLES:
        platform = table in PLATFORM_CAPABLE
        rule = (
            "((tenant_id IS NOT NULL AND tenant_id = "
            "nullif(current_setting('app.tenant_id', true), '')::uuid) OR (tenant_id IS NULL AND "
            "current_setting('app.evidence_scope', true) = 'platform-security'))"
            if platform
            else "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
        )
        _run(f"""
            ALTER TABLE evidence.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE evidence.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON evidence.{table} USING {rule} WITH CHECK {rule};
        """)
    # Destroyed subject keys and erased resolver rows can never come back to life.
    _run("""
        CREATE FUNCTION evidence.no_resurrection() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_state text := to_jsonb(OLD) ->> CASE TG_TABLE_NAME
                WHEN 'subject_key' THEN 'state' ELSE 'status' END;
            gone boolean := CASE TG_TABLE_NAME
                WHEN 'subject_key' THEN old_state = 'DESTROYED'
                WHEN 'subject_handle_map' THEN old_state = 'ERASED'
                ELSE old_state <> 'STORED' END;
        BEGIN
            IF gone THEN
                RAISE EXCEPTION 'evidence.%: erased material cannot be restored', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF TG_TABLE_NAME = 'evidence_object'
               AND (to_jsonb(NEW) ->> 'content_hash') <> (to_jsonb(OLD) ->> 'content_hash') THEN
                RAISE EXCEPTION 'evidence.evidence_object content is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER subject_key_no_resurrection BEFORE UPDATE ON evidence.subject_key
            FOR EACH ROW EXECUTE FUNCTION evidence.no_resurrection();
        CREATE TRIGGER subject_handle_map_no_resurrection BEFORE UPDATE ON evidence.subject_handle_map
            FOR EACH ROW EXECUTE FUNCTION evidence.no_resurrection();
        CREATE TRIGGER evidence_object_no_resurrection BEFORE UPDATE ON evidence.evidence_object
            FOR EACH ROW EXECUTE FUNCTION evidence.no_resurrection();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE evidence.decision_aggregate;
        DROP TABLE evidence.legal_hold;
        DROP TABLE evidence.evidence_object;
        DROP TABLE evidence.subject_handle_map;
        DROP TABLE evidence.subject_key;
        DROP FUNCTION evidence.no_resurrection();
        DROP TABLE evidence.erasure_tombstone;
        DROP TABLE evidence.custody_record;
        DROP TABLE evidence.receipt_annotation;
        DROP TABLE evidence.audit_record;
        DROP TABLE evidence.decision_receipt;
    """)
