"""WF08 evidence services: signed authority history, signed status lists, export tiers with named recipients (ZID-CP-WF08 v2.0 E02, E06, E09; CR-170).

Revision ID: evidence_0011
Revises: evidence_0010
Create Date: 2026-11-25

authority_snapshot, status_slot, status_change and status_publication are append-only (the evidence module's own mutation guard) and take their time from the
database clock. A snapshot is written once, when a decision is made, and a later revocation never edits it. A status slot is a random position in a list;
revoking or reinstating is a new row in status_change, never an edit. A published list is signed JSON that is public by design, so it alone has a second,
read-only policy for an unauthenticated lookup (the session sets app.public_status_list for exactly that one read).

export_request moves through states (REQUESTED, APPROVED, REFUSED, ISSUED) and the database keeps the two-person rule: whoever decides is never whoever asked.
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0011"
down_revision = "evidence_0010"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
APPEND_ONLY = ("authority_snapshot", "status_slot", "status_change", "status_publication")


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.authority_snapshot (
            snapshot_id     uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            decision_ref    text NOT NULL CHECK (char_length(decision_ref) BETWEEN 1 AND 128),
            subject_ref     text NOT NULL CHECK (char_length(subject_ref) BETWEEN 1 AND 128),
            decision_at     timestamptz NOT NULL,
            verdict         text NOT NULL CHECK (verdict IN ('COMPLETE', 'INDETERMINATE')),
            reasons         text[] NOT NULL DEFAULT '{{}}',
            body            jsonb NOT NULL,
            snapshot_digest text NOT NULL CHECK (snapshot_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            signature       text NOT NULL,
            signer_kid      text NOT NULL,
            created_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, decision_ref)
        );

        CREATE TABLE evidence.status_list (
            list_id    uuid PRIMARY KEY,
            tenant_id  uuid NOT NULL,
            purpose    text NOT NULL CHECK (purpose IN ('revocation', 'suspension')),
            size_bits  integer NOT NULL CHECK (size_bits BETWEEN 131072 AND 8388608 AND size_bits % 8 = 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, purpose)
        );
        CREATE TABLE evidence.status_slot (
            slot_id      uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            list_id      uuid NOT NULL REFERENCES evidence.status_list (list_id),
            position     integer NOT NULL CHECK (position >= 0),
            subject_ref  text NOT NULL CHECK (char_length(subject_ref) BETWEEN 1 AND 128),
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (list_id, position),
            UNIQUE (list_id, subject_ref)
        );
        CREATE TABLE evidence.status_change (
            change_id   uuid PRIMARY KEY,
            seq         bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id   uuid NOT NULL,
            slot_id     uuid NOT NULL REFERENCES evidence.status_slot (slot_id),
            change      text NOT NULL CHECK (change IN ('SET', 'REINSTATE')),
            reason      text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 300),
            changed_by  uuid,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX status_change_slot ON evidence.status_change (slot_id, seq DESC);
        CREATE TABLE evidence.status_publication (
            publication_id uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            list_id        uuid NOT NULL REFERENCES evidence.status_list (list_id),
            version        integer NOT NULL CHECK (version >= 1),
            jws            text NOT NULL,
            issued_at      timestamptz NOT NULL,
            expires_at     timestamptz NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (list_id, version),
            CHECK (expires_at > issued_at)
        );

        CREATE TABLE evidence.export_request (
            request_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            receipt_id          uuid NOT NULL,
            tier                text NOT NULL CHECK (tier IN ('PUBLIC_MINIMAL', 'CUSTOMER_EVIDENCE', 'AUDITOR_DOSSIER')),
            recipient           jsonb NOT NULL DEFAULT '{{}}',
            purpose             text NOT NULL DEFAULT '' CHECK (char_length(purpose) <= 300),
            legal_basis         text,
            retention_days      integer NOT NULL CHECK (retention_days BETWEEN 1 AND 365),
            recipient_country   text CHECK (recipient_country ~ '^[A-Z]{{2}}$'),
            cross_border        boolean NOT NULL DEFAULT false,
            needs_second_person boolean NOT NULL,
            state               text NOT NULL DEFAULT 'REQUESTED' CHECK (state IN ('REQUESTED', 'APPROVED', 'REFUSED', 'ISSUED')),
            requested_by        uuid NOT NULL,
            requested_at        timestamptz NOT NULL DEFAULT now(),
            decided_by          uuid,
            decided_at          timestamptz,
            decision_reason     text CHECK (char_length(decision_reason) <= 300),
            issued_at           timestamptz,
            expires_at          timestamptz,
            package_digest      text CHECK (package_digest ~ '^sha-256:[0-9a-f]{{64}}$'),
            manifest_jws        text,
            -- the two-person rule is kept by the database as well as by the service
            CHECK (decided_by IS NULL OR decided_by <> requested_by),
            CHECK (state <> 'ISSUED' OR (package_digest IS NOT NULL AND manifest_jws IS NOT NULL AND expires_at IS NOT NULL)),
            CHECK (state <> 'ISSUED' OR NOT needs_second_person OR decided_by IS NOT NULL),
            CHECK (state <> 'REFUSED' OR decision_reason IS NOT NULL)
        );
        CREATE INDEX export_request_tenant ON evidence.export_request (tenant_id, requested_at DESC);

        ALTER TABLE evidence.export_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.export_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.export_request USING {RULE} WITH CHECK {RULE};
        ALTER TABLE evidence.status_list ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.status_list FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.status_list USING {RULE} WITH CHECK {RULE};
    """)
    for table in APPEND_ONLY:
        _run(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON evidence.{table}
                FOR EACH ROW EXECUTE FUNCTION evidence.refuse_mutation();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON evidence.{table}
                FOR EACH STATEMENT EXECUTE FUNCTION evidence.refuse_mutation();
            CREATE TRIGGER {table}_db_time BEFORE INSERT ON evidence.{table}
                FOR EACH ROW EXECUTE FUNCTION evidence.require_db_time();
            ALTER TABLE evidence.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE evidence.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON evidence.{table} USING {RULE} WITH CHECK {RULE};
        """)
    _run("""
        CREATE POLICY public_status_list_read ON evidence.status_publication FOR SELECT
            USING (current_setting('app.public_status_list', true) = 'on');
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE evidence.export_request;
        DROP TABLE evidence.status_publication;
        DROP TABLE evidence.status_change;
        DROP TABLE evidence.status_slot;
        DROP TABLE evidence.status_list;
        DROP TABLE evidence.authority_snapshot;
    """)
