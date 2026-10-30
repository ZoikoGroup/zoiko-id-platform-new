"""Sanctions lists, screening cases and signed screening evidence (ZID-ED-04; CR-043).

Revision ID: screening_0001
Revises:
Create Date: 2026-10-30

sanction_list_version, sanction_list_entry: the public lists as loaded (not tenant data): one active version per list, with its
own reference, content hash and fetch time, so a decision can cite exactly what was checked.
screening_case, screening_party, screening_ownership: one screening of one company for one tenant. A party holds a name and a
birth year, so the rows are restricted records in the tenant's home cell (row-level security, never in events or logs).
screening_match: every alert of every run, immutable. screening_evidence: append-only, one signed row per decision, refresh or
reviewer ruling.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "screening_case",
    "screening_party",
    "screening_ownership",
    "screening_match",
    "screening_evidence",
)
_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.sanction_list_version (
            version_id    uuid PRIMARY KEY,
            list_id       text NOT NULL CHECK (list_id IN ('OFAC_SDN', 'OFAC_NON_SDN', 'UK_SANCTIONS', 'EU_CONSOLIDATED',
                      'UN_SC', 'LOCAL')),
            version       text NOT NULL CHECK (length(version) BETWEEN 1 AND 128),
            content_hash  text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            entry_count   integer NOT NULL CHECK (entry_count > 0),
            source_ref    text NOT NULL,
            fetched_at    timestamptz NOT NULL,
            active        boolean NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL,
            UNIQUE (list_id, content_hash)
        );
        CREATE UNIQUE INDEX sanction_list_version_active ON verify.sanction_list_version (list_id) WHERE active;

        CREATE TABLE verify.sanction_list_entry (
            version_id    uuid NOT NULL REFERENCES verify.sanction_list_version (version_id) ON DELETE CASCADE,
            source_ref    text NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('PERSON', 'ENTITY', 'OTHER')),
            primary_name  text NOT NULL,
            data          jsonb NOT NULL,
            PRIMARY KEY (version_id, source_ref)
        );

        CREATE TABLE verify.screening_case (
            case_id          uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            kyb_case_id      uuid,
            country          text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            idempotency_key  text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            status           text NOT NULL CHECK (status IN ('NOT_STARTED', 'GATHERING', 'SCREENING', 'VERIFIED',
                             'REVIEW_REQUIRED', 'REJECTED', 'UNAVAILABLE', 'EXPIRED', 'SUSPENDED')),
            reasons          jsonb NOT NULL DEFAULT '[]',
            elevated_risk    boolean NOT NULL DEFAULT false,
            authority        jsonb NOT NULL,
            requested_by     uuid NOT NULL,
            current_version  integer NOT NULL DEFAULT 0,
            expiry_at        timestamptz,
            created_at       timestamptz NOT NULL,
            updated_at       timestamptz NOT NULL,
            UNIQUE (tenant_id, idempotency_key)
        );

        CREATE TABLE verify.screening_party (
            tenant_id   uuid NOT NULL,
            case_id     uuid NOT NULL REFERENCES verify.screening_case (case_id),
            ref         text NOT NULL CHECK (length(ref) BETWEEN 1 AND 64),
            role        text NOT NULL CHECK (role IN ('ENTITY', 'REPRESENTATIVE', 'DIRECTOR', 'OWNER')),
            kind        text NOT NULL CHECK (kind IN ('PERSON', 'ENTITY', 'OTHER')),
            name        text NOT NULL,
            birth       text,
            countries   jsonb NOT NULL DEFAULT '[]',
            identifiers jsonb NOT NULL DEFAULT '[]',
            PRIMARY KEY (case_id, ref)
        );

        CREATE TABLE verify.screening_ownership (
            tenant_id uuid NOT NULL,
            case_id   uuid PRIMARY KEY REFERENCES verify.screening_case (case_id),
            graph     jsonb NOT NULL
        );

        CREATE TABLE verify.screening_match (
            match_id         uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            case_id          uuid NOT NULL REFERENCES verify.screening_case (case_id),
            evidence_version integer NOT NULL,
            party_ref        text NOT NULL,
            list_id          text NOT NULL,
            list_version     text NOT NULL,
            source_ref       text NOT NULL,
            matched_name     text NOT NULL,
            score            numeric(5, 4) NOT NULL,
            strength         text NOT NULL CHECK (strength IN ('STRONG', 'POSSIBLE')),
            reasons          jsonb NOT NULL,
            created_at       timestamptz NOT NULL
        );

        CREATE TABLE verify.screening_evidence (
            evidence_id     uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            case_id         uuid NOT NULL REFERENCES verify.screening_case (case_id),
            version         integer NOT NULL CHECK (version >= 1),
            decision_status text NOT NULL CHECK (decision_status IN ('VERIFIED', 'REVIEW_REQUIRED', 'REJECTED',
                            'UNAVAILABLE', 'GATHERING')),
            reasons         jsonb NOT NULL,
            body            jsonb NOT NULL,
            evidence_hash   text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
            jws             text NOT NULL,
            decided_by      uuid,
            rationale       text,
            expiry_at       timestamptz,
            created_at      timestamptz NOT NULL,
            UNIQUE (case_id, version)
        );
        CREATE FUNCTION verify.screening_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'screening evidence and matches are append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER screening_evidence_append_only BEFORE UPDATE OR DELETE ON verify.screening_evidence
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();
        CREATE TRIGGER screening_match_append_only BEFORE UPDATE OR DELETE ON verify.screening_match
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    _run(
        "DROP TABLE verify.screening_evidence, verify.screening_match, verify.screening_ownership, "
        "verify.screening_party, verify.screening_case, verify.sanction_list_entry, verify.sanction_list_version; "
        "DROP FUNCTION verify.screening_append_only();"
    )
