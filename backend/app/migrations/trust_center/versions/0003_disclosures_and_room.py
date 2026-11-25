"""Privacy disclosures and the restricted evidence room (ZID-CP-WF12 v2.0, WF-12.05 and WF-12.03; CR-174).

Revision ID: trust_center_0003
Revises: trust_center_0002
Create Date: 2026-11-25

tc_disclosure        a subprocessor, retention rule, biometric statement or transfer condition for one market, as a numbered revision. Content never changes (a change
                     is a new revision); it becomes public only when someone other than its author approved it, and a newer approved revision supersedes the older.
tc_evidence_blob     the bytes of a restricted evidence artifact, scanned before they are kept. Never served directly, never indexed.
tc_room_request      a signed-in business's request for one artifact, with its purpose and its acceptance of the confidentiality terms.
tc_room_grant        what an approver allowed: an expiring, revocable access bound to the requester (a forwarded link fails), with a download limit and a watermark text.
tc_room_access       append-only: every view or download attempt and its outcome.
"""

from zid_platform.migrations import execute_script as _run

revision = "trust_center_0003"
down_revision = "trust_center_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tc_disclosure (
            disclosure_id  uuid PRIMARY KEY,
            kind           text NOT NULL CHECK (kind IN ('subprocessor', 'retention', 'biometric', 'transfer')),
            subject        text NOT NULL CHECK (length(btrim(subject)) BETWEEN 2 AND 160),
            market         text NOT NULL CHECK (market ~ '^([A-Z]{2}|GLOBAL)$'),
            revision       integer NOT NULL CHECK (revision >= 1),
            payload        jsonb NOT NULL,
            effective_from date NOT NULL,
            author         uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            approved_by    uuid,
            approved_at    timestamptz,
            status         text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'WITHDRAWN')),
            UNIQUE (kind, subject, market, revision),
            CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
            CHECK (approved_by IS NULL OR approved_by <> author),
            CHECK (status NOT IN ('ACTIVE', 'SUPERSEDED') OR approved_by IS NOT NULL)
        );
        CREATE INDEX tc_disclosure_public ON platform.tc_disclosure (market, kind) WHERE status = 'ACTIVE';
        CREATE FUNCTION platform.tc_disclosure_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a disclosure is never deleted; withdraw it' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.disclosure_id, NEW.kind, NEW.subject, NEW.market, NEW.revision, NEW.payload, NEW.effective_from, NEW.author, NEW.created_at)
               IS DISTINCT FROM (OLD.disclosure_id, OLD.kind, OLD.subject, OLD.market, OLD.revision, OLD.payload, OLD.effective_from, OLD.author, OLD.created_at) THEN
                RAISE EXCEPTION 'the content of a disclosure never changes; write a new revision' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.approved_by IS NOT NULL AND NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
                RAISE EXCEPTION 'an approval is not changed' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'WITHDRAWN' AND NEW.status <> 'WITHDRAWN' THEN
                RAISE EXCEPTION 'a withdrawn disclosure stays withdrawn' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_disclosure_guard BEFORE UPDATE OR DELETE ON platform.tc_disclosure
            FOR EACH ROW EXECUTE FUNCTION platform.tc_disclosure_guard();

        CREATE TABLE platform.tc_evidence_blob (
            evidence_id  uuid PRIMARY KEY REFERENCES platform.tc_evidence (evidence_id),
            content      bytea NOT NULL,
            content_type text NOT NULL CHECK (content_type IN ('application/pdf', 'text/plain', 'text/csv', 'application/json')),
            size_bytes   integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 20000000),
            scan_state   text NOT NULL CHECK (scan_state = 'CLEAN'),
            uploaded_by  uuid NOT NULL,
            uploaded_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE FUNCTION platform.tc_blob_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a stored artifact is replaced by recording new evidence' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER tc_blob_append_only BEFORE UPDATE OR DELETE ON platform.tc_evidence_blob
            FOR EACH ROW EXECUTE FUNCTION platform.tc_blob_guard();

        CREATE TABLE platform.tc_room_request (
            request_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            requester       uuid NOT NULL,
            organization    text NOT NULL CHECK (length(btrim(organization)) BETWEEN 2 AND 200),
            purpose         text NOT NULL CHECK (length(btrim(purpose)) BETWEEN 10 AND 1000),
            evidence_id     uuid NOT NULL REFERENCES platform.tc_evidence (evidence_id),
            terms_accepted_at timestamptz NOT NULL,
            terms_version   text NOT NULL,
            status          text NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED', 'APPROVED', 'DENIED', 'REVOKED')),
            decided_by      uuid,
            decided_at      timestamptz,
            decision_reason text NOT NULL DEFAULT '',
            created_at      timestamptz NOT NULL DEFAULT now(),
            CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
            CHECK (status = 'REQUESTED' OR decided_by IS NOT NULL),
            CHECK (decided_by IS NULL OR decided_by <> requester)
        );
        CREATE INDEX tc_room_request_tenant ON platform.tc_room_request (tenant_id, created_at DESC);
        CREATE FUNCTION platform.tc_room_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a request is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.request_id, NEW.tenant_id, NEW.requester, NEW.organization, NEW.purpose, NEW.evidence_id, NEW.terms_accepted_at, NEW.terms_version, NEW.created_at)
               IS DISTINCT FROM (OLD.request_id, OLD.tenant_id, OLD.requester, OLD.organization, OLD.purpose, OLD.evidence_id, OLD.terms_accepted_at, OLD.terms_version, OLD.created_at) THEN
                RAISE EXCEPTION 'a request is not edited' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status IN ('DENIED', 'REVOKED') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'a denied or revoked request stays so' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_room_request_guard BEFORE UPDATE OR DELETE ON platform.tc_room_request
            FOR EACH ROW EXECUTE FUNCTION platform.tc_room_request_guard();

        CREATE TABLE platform.tc_room_grant (
            grant_id       uuid PRIMARY KEY,
            request_id     uuid NOT NULL UNIQUE REFERENCES platform.tc_room_request (request_id),
            token_hash     text NOT NULL UNIQUE,
            expires_at     timestamptz NOT NULL,
            max_downloads  integer NOT NULL CHECK (max_downloads BETWEEN 1 AND 20),
            downloads      integer NOT NULL DEFAULT 0 CHECK (downloads >= 0),
            watermark      text NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            revoked_at     timestamptz,
            revoked_by     uuid,
            CHECK ((revoked_by IS NULL) = (revoked_at IS NULL)),
            CHECK (downloads <= max_downloads)
        );

        CREATE TABLE platform.tc_room_access (
            access_id  uuid PRIMARY KEY,
            grant_id   uuid NOT NULL REFERENCES platform.tc_room_grant (grant_id),
            at         timestamptz NOT NULL DEFAULT now(),
            principal  uuid,
            action     text NOT NULL CHECK (action IN ('VIEW', 'DOWNLOAD')),
            outcome    text NOT NULL,
            source_hash text NOT NULL DEFAULT ''
        );
        CREATE INDEX tc_room_access_grant ON platform.tc_room_access (grant_id, at);
        CREATE FUNCTION platform.tc_room_access_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the access history is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER tc_room_access_append_only BEFORE UPDATE OR DELETE ON platform.tc_room_access
            FOR EACH ROW EXECUTE FUNCTION platform.tc_room_access_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.tc_room_access;
        DROP FUNCTION platform.tc_room_access_guard();
        DROP TABLE platform.tc_room_grant;
        DROP TABLE platform.tc_room_request;
        DROP FUNCTION platform.tc_room_request_guard();
        DROP TABLE platform.tc_evidence_blob;
        DROP FUNCTION platform.tc_blob_guard();
        DROP TABLE platform.tc_disclosure;
        DROP FUNCTION platform.tc_disclosure_guard();
    """)
