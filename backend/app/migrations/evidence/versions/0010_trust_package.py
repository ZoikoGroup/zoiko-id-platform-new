"""Governed CSCA trust store: staged, validated, independently approved, activated and rolled back (ZID-DTV-WF-01 section 13, O2; CR-107).

Revision ID: evidence_0010
Revises: evidence_0009
Create Date: 2026-11-17

A package is one imported master list (the bytes as downloaded, so validation can be repeated), the anchors the stager confirmed out of band, and the
certificates that came out of validating it. Importing is not trusting: a package only becomes the active trust set after validation and after a person
other than the stager approved and activated it. `version` counts every change of one package (it feeds the outbox aggregate version); `seq` numbers the
packages of a tenant. At most one package per tenant and kind is ACTIVE (a partial unique index says so).
"""

from zid_platform.migrations import execute_script as _run

revision = "evidence_0010"
down_revision = "evidence_0009"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE evidence.trust_package (
            package_id          uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            kind                text NOT NULL CHECK (kind IN ('CSCA')),
            seq                 integer NOT NULL CHECK (seq >= 1),
            version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            state               text NOT NULL DEFAULT 'STAGED' CHECK (state IN (
                'STAGED', 'VALIDATED', 'REJECTED', 'APPROVED', 'ACTIVE', 'SUPERSEDED', 'ROLLED_BACK', 'RETIRED')),
            source_uri          text NOT NULL CHECK (char_length(source_uri) BETWEEN 3 AND 500),
            source_note         text CHECK (char_length(source_note) <= 500),
            package_sha256      text NOT NULL CHECK (package_sha256 ~ '^[0-9a-f]{{64}}$'),
            package_der         bytea NOT NULL CHECK (octet_length(package_der) BETWEEN 1 AND 8388608),
            anchor_pems         text[] NOT NULL CHECK (cardinality(anchor_pems) BETWEEN 1 AND 5),
            anchor_sha256       text[] NOT NULL CHECK (cardinality(anchor_sha256) BETWEEN 1 AND 5),
            list_status         text CHECK (list_status IN ('VERIFIED', 'NOT_VERIFIED', 'INDETERMINATE')),
            list_reasons        text[] NOT NULL DEFAULT '{{}}',
            list_signer         text,
            certificates        jsonb NOT NULL DEFAULT '[]'::jsonb,
            certificate_count   integer NOT NULL DEFAULT 0 CHECK (certificate_count >= 0),
            manifest_digest     text CHECK (manifest_digest ~ '^[0-9a-f]{{64}}$'),
            staged_by           uuid NOT NULL,
            staged_at           timestamptz NOT NULL,
            validated_at        timestamptz,
            decided_by          uuid,
            decided_at          timestamptz,
            decision_reason     text CHECK (char_length(decision_reason) <= 500),
            activated_by        uuid,
            activated_at        timestamptz,
            ended_at            timestamptz,
            ended_by            uuid,
            ended_reason        text CHECK (char_length(ended_reason) <= 500),
            UNIQUE (tenant_id, kind, seq),
            -- an approved or active package was validated, has certificates and a manifest
            CHECK (state NOT IN ('APPROVED', 'ACTIVE', 'SUPERSEDED', 'ROLLED_BACK', 'RETIRED')
                   OR (list_status = 'VERIFIED' AND certificate_count >= 1 AND manifest_digest IS NOT NULL)),
            -- the two-person rule, kept by the database as well as by the service
            CHECK (decided_by IS NULL OR decided_by <> staged_by),
            CHECK (activated_by IS NULL OR activated_by <> staged_by),
            CHECK (state <> 'REJECTED' OR decision_reason IS NOT NULL)
        );
        CREATE UNIQUE INDEX trust_package_one_active ON evidence.trust_package (tenant_id, kind) WHERE state = 'ACTIVE';
        CREATE INDEX trust_package_tenant ON evidence.trust_package (tenant_id, kind, seq DESC);
        ALTER TABLE evidence.trust_package ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence.trust_package FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON evidence.trust_package USING {RULE} WITH CHECK {RULE};
    """)


def downgrade() -> None:
    _run("DROP TABLE evidence.trust_package;")
