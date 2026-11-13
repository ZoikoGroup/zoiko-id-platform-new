"""Approval of the public pricing page's packaging, and the privacy notice shown beside its inquiry form (ZID-UX-PRICE-004 section 09, WF-P07).

Revision ID: commercial_0039
Revises: commercial_0038
Create Date: 2026-11-13

pricing_page_signature: who signed which version of `pricing-page.yaml`, by its content hash. The page counts as APPROVED only while the current file's hash is signed by an appointed Founder
and an appointed CCO who are different people; changing the file makes it unsigned again, so the "plan contents are being finalised" notice returns by itself. Append-only.

public_notice: the privacy notice text shown beside a public form, versioned. A notice starts as a DRAFT, is activated by a different person than its author, and is then immutable;
the next version supersedes it. Legal writes the text; nothing here invents it. A form records the version it showed, and an inquiry that cites a version that is no longer the one served is refused.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0039"
down_revision = "commercial_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE commercial.pricing_page_signature (
            content_hash     text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            page_version     text NOT NULL,
            signer_principal uuid NOT NULL,
            signer_role      text NOT NULL CHECK (signer_role IN ('FOUNDER', 'CCO')),
            signed_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (content_hash, signer_principal),
            UNIQUE (content_hash, signer_role)
        );
        CREATE TRIGGER pricing_page_signature_immutable BEFORE UPDATE OR DELETE ON commercial.pricing_page_signature
            FOR EACH ROW EXECUTE FUNCTION commercial.immutable_guard();

        CREATE TABLE commercial.public_notice (
            notice_id     uuid PRIMARY KEY,
            kind          text NOT NULL CHECK (kind IN ('PRICING_INQUIRY')),
            version       text NOT NULL CHECK (version ~ '^[A-Za-z0-9._-]{3,40}$'),
            body          text NOT NULL CHECK (char_length(btrim(body)) BETWEEN 40 AND 4000),
            status        text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'WITHDRAWN')),
            created_by    uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            activated_by  uuid,
            activated_at  timestamptz,
            UNIQUE (kind, version),
            CHECK ((status IN ('ACTIVE', 'SUPERSEDED')) = (activated_by IS NOT NULL AND activated_at IS NOT NULL) OR status IN ('DRAFT', 'WITHDRAWN')),
            CHECK (activated_by IS NULL OR activated_by <> created_by)
        );
        CREATE UNIQUE INDEX public_notice_one_active ON commercial.public_notice (kind) WHERE status = 'ACTIVE';
        CREATE FUNCTION commercial.public_notice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a notice version is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.notice_id, NEW.kind, NEW.version, NEW.body, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM (OLD.notice_id, OLD.kind, OLD.version, OLD.body, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'a notice version is never edited: write a new one' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                OLD.status = NEW.status
                OR (OLD.status = 'DRAFT' AND NEW.status IN ('ACTIVE', 'WITHDRAWN'))
                OR (OLD.status = 'ACTIVE' AND NEW.status = 'SUPERSEDED')
            ) THEN
                RAISE EXCEPTION 'that notice status change is not allowed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER public_notice_guard BEFORE UPDATE OR DELETE ON commercial.public_notice
            FOR EACH ROW EXECUTE FUNCTION commercial.public_notice_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE commercial.public_notice;
        DROP TABLE commercial.pricing_page_signature;
        DROP FUNCTION commercial.public_notice_guard();
    """)
