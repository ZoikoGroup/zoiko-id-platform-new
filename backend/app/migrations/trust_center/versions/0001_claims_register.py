"""The Trust Center's claims register: evidence, claim revisions, hash-bound approvals, publications and emergency withdrawals
(ZID-CP-WF12 v2.0, WF-12.09 to 12.11; CR-174).

Revision ID: trust_center_0001
Revises:
Create Date: 2026-11-25

Company records, not tenant content, so not tenant-scoped (like the operations module's incidents). Nothing here is customer data.

tc_evidence         an artifact a claim can rest on: its issuer, the exact scope it was assessed for and its digest. The facts never change; only its lifecycle and
                    the issuer's confirmation do. Whoever recorded it cannot be the one who confirms it.
tc_claim_revision   one version of one public sentence. The content never changes (a new text is a new revision); only the status moves, and never out of EMERGENCY_WITHDRAWN.
tc_approval         one reviewer's approval or refusal of one exact content hash. Append-only. The database refuses the author's own approval, an approval for a hash that is
                    not the revision's, and a second approval from the same person or the same role.
tc_publication      a revision put live (or rolled back to). Append-only. The database refuses a publication without two approvals from two distinct people other than the
                    author, a publisher who is the author, a withdrawn revision, and a rollback to a revision that was never published.
tc_withdrawal       an emergency withdrawal: who, why, when, and the retrospective review it demands (by someone other than the responder).
"""

from zid_platform.migrations import execute_script as _run

revision = "trust_center_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tc_evidence (
            evidence_id     uuid PRIMARY KEY,
            digest          text NOT NULL UNIQUE CHECK (digest ~ '^[0-9a-f]{64}$'),
            evidence_type   text NOT NULL CHECK (evidence_type IN ('report', 'attestation', 'certificate', 'assessment',
                            'registration', 'self_assessment', 'declaration', 'contract_commitment', 'program_listing')),
            scheme          text NOT NULL CHECK (length(scheme) BETWEEN 2 AND 80),
            issuer          text NOT NULL CHECK (length(issuer) BETWEEN 2 AND 160),
            assessor        text NOT NULL DEFAULT '',
            legal_entity    text NOT NULL CHECK (length(legal_entity) BETWEEN 2 AND 160),
            products        text[] NOT NULL CHECK (cardinality(products) >= 1),
            regions         text[] NOT NULL DEFAULT '{}',
            exclusions      text[] NOT NULL DEFAULT '{}',
            period_start    date NOT NULL,
            period_end      date NOT NULL,
            holder          text NOT NULL CHECK (length(holder) BETWEEN 2 AND 160),
            restricted      boolean NOT NULL DEFAULT false,
            lifecycle       text NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle IN ('ACTIVE', 'SUPERSEDED', 'EXPIRED', 'SUSPENDED', 'WITHDRAWN')),
            issuer_confirmed boolean NOT NULL DEFAULT false,
            confirmed_by    uuid,
            confirmed_at    timestamptz,
            recorded_by     uuid NOT NULL,
            recorded_at     timestamptz NOT NULL DEFAULT now(),
            CHECK (period_end >= period_start),
            CHECK (issuer_confirmed = (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
            CHECK (confirmed_by IS NULL OR confirmed_by <> recorded_by)
        );
        CREATE FUNCTION platform.tc_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'evidence is never deleted; mark it withdrawn' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.evidence_id, NEW.digest, NEW.evidence_type, NEW.scheme, NEW.issuer, NEW.assessor, NEW.legal_entity, NEW.products, NEW.regions,
                NEW.exclusions, NEW.period_start, NEW.period_end, NEW.holder, NEW.restricted, NEW.recorded_by, NEW.recorded_at)
               IS DISTINCT FROM
               (OLD.evidence_id, OLD.digest, OLD.evidence_type, OLD.scheme, OLD.issuer, OLD.assessor, OLD.legal_entity, OLD.products, OLD.regions,
                OLD.exclusions, OLD.period_start, OLD.period_end, OLD.holder, OLD.restricted, OLD.recorded_by, OLD.recorded_at) THEN
                RAISE EXCEPTION 'the facts of an evidence record never change; record new evidence' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.lifecycle = 'WITHDRAWN' AND NEW.lifecycle <> 'WITHDRAWN' THEN
                RAISE EXCEPTION 'withdrawn evidence stays withdrawn' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_evidence_guard BEFORE UPDATE OR DELETE ON platform.tc_evidence
            FOR EACH ROW EXECUTE FUNCTION platform.tc_evidence_guard();

        CREATE TABLE platform.tc_claim_revision (
            claim_id         uuid NOT NULL,
            revision         integer NOT NULL CHECK (revision >= 1),
            exact_text       text NOT NULL CHECK (length(exact_text) BETWEEN 3 AND 2000),
            locale           text NOT NULL CHECK (locale ~ '^[a-z]{2}(-[A-Z]{2})?$'),
            claim_type       text NOT NULL CHECK (claim_type IN ('numerical', 'certification', 'third_party_assessment', 'legal',
                             'customer_endorsement', 'sla', 'functionality', 'roadmap')),
            scope            jsonb NOT NULL,
            channels         text[] NOT NULL CHECK (cardinality(channels) >= 1),
            evidence_digests text[] NOT NULL DEFAULT '{}',
            valid_until      date,
            author           uuid NOT NULL,
            content_hash     text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            status           text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVAL_PENDING', 'PUBLISHED', 'EXPIRED',
                             'SUPERSEDED', 'EMERGENCY_WITHDRAWN')),
            created_at       timestamptz NOT NULL DEFAULT now(),
            published_at     timestamptz,
            withdrawn_at     timestamptz,
            PRIMARY KEY (claim_id, revision),
            UNIQUE (claim_id, revision, content_hash)
        );
        CREATE INDEX tc_claim_live ON platform.tc_claim_revision (status, claim_id);
        CREATE FUNCTION platform.tc_claim_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a claim revision is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.claim_id, NEW.revision, NEW.exact_text, NEW.locale, NEW.claim_type, NEW.scope, NEW.channels, NEW.evidence_digests,
                NEW.valid_until, NEW.author, NEW.content_hash, NEW.created_at)
               IS DISTINCT FROM
               (OLD.claim_id, OLD.revision, OLD.exact_text, OLD.locale, OLD.claim_type, OLD.scope, OLD.channels, OLD.evidence_digests,
                OLD.valid_until, OLD.author, OLD.content_hash, OLD.created_at) THEN
                RAISE EXCEPTION 'the content of a claim revision never changes; write a new revision' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status = 'EMERGENCY_WITHDRAWN' AND NEW.status <> 'EMERGENCY_WITHDRAWN' THEN
                RAISE EXCEPTION 'a withdrawn claim revision cannot be put back; submit a new revision' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = 'PUBLISHED' AND OLD.status NOT IN ('PUBLISHED', 'APPROVAL_PENDING', 'DRAFT', 'SUPERSEDED') THEN
                RAISE EXCEPTION 'only a draft, pending or earlier approved revision can go live' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_claim_guard BEFORE UPDATE OR DELETE ON platform.tc_claim_revision
            FOR EACH ROW EXECUTE FUNCTION platform.tc_claim_guard();

        CREATE TABLE platform.tc_approval (
            approval_id      uuid PRIMARY KEY,
            claim_id         uuid NOT NULL,
            revision         integer NOT NULL,
            claim_hash       text NOT NULL,
            approver         uuid NOT NULL,
            role             text NOT NULL CHECK (role IN ('product', 'legal', 'security', 'privacy', 'sre', 'finance', 'marketing')),
            evidence_digests text[] NOT NULL DEFAULT '{}',
            refused          boolean NOT NULL DEFAULT false,
            reason           text NOT NULL DEFAULT '',
            decided_at       timestamptz NOT NULL DEFAULT now(),
            effective_until  timestamptz,
            FOREIGN KEY (claim_id, revision, claim_hash) REFERENCES platform.tc_claim_revision (claim_id, revision, content_hash),
            CHECK (NOT refused OR length(btrim(reason)) > 0)
        );
        CREATE UNIQUE INDEX tc_approval_one_person ON platform.tc_approval (claim_id, revision, approver) WHERE NOT refused;
        CREATE UNIQUE INDEX tc_approval_one_role ON platform.tc_approval (claim_id, revision, role) WHERE NOT refused;
        CREATE FUNCTION platform.tc_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE who uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'approvals are append-only; a later decision is a new row' USING ERRCODE = 'check_violation';
            END IF;
            SELECT author INTO who FROM platform.tc_claim_revision WHERE claim_id = NEW.claim_id AND revision = NEW.revision;
            IF who = NEW.approver THEN
                RAISE EXCEPTION 'the author of a claim cannot review it' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.tc_approval
            FOR EACH ROW EXECUTE FUNCTION platform.tc_approval_guard();

        CREATE TABLE platform.tc_publication (
            publication_id   uuid PRIMARY KEY,
            claim_id         uuid NOT NULL,
            revision         integer NOT NULL,
            claim_hash       text NOT NULL,
            kind             text NOT NULL CHECK (kind IN ('PUBLISH', 'ROLLBACK')),
            rolled_back_from integer,
            channels         text[] NOT NULL,
            locale           text NOT NULL,
            manifest_digest  text NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
            published_by     uuid NOT NULL,
            published_at     timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (claim_id, revision, claim_hash) REFERENCES platform.tc_claim_revision (claim_id, revision, content_hash),
            CHECK ((kind = 'ROLLBACK') = (rolled_back_from IS NOT NULL))
        );
        CREATE INDEX tc_publication_claim ON platform.tc_publication (claim_id, published_at DESC);
        CREATE FUNCTION platform.tc_publication_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE who uuid; st text; people integer;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'publications are append-only' USING ERRCODE = 'check_violation';
            END IF;
            SELECT author, status INTO who, st FROM platform.tc_claim_revision WHERE claim_id = NEW.claim_id AND revision = NEW.revision;
            IF st = 'EMERGENCY_WITHDRAWN' THEN
                RAISE EXCEPTION 'a withdrawn revision cannot be published' USING ERRCODE = 'check_violation';
            END IF;
            IF who = NEW.published_by THEN
                RAISE EXCEPTION 'the author of a claim cannot publish it' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.kind = 'PUBLISH' THEN
                SELECT count(DISTINCT approver) INTO people FROM platform.tc_approval
                 WHERE claim_id = NEW.claim_id AND revision = NEW.revision AND claim_hash = NEW.claim_hash AND NOT refused
                   AND (effective_until IS NULL OR effective_until > now());
                IF people < 2 THEN
                    RAISE EXCEPTION 'publication needs approvals from two people other than the author' USING ERRCODE = 'check_violation';
                END IF;
                IF EXISTS (SELECT 1 FROM platform.tc_approval WHERE claim_id = NEW.claim_id AND revision = NEW.revision
                           AND claim_hash = NEW.claim_hash AND refused) THEN
                    RAISE EXCEPTION 'a reviewer refused this revision' USING ERRCODE = 'check_violation';
                END IF;
            ELSE
                IF NOT EXISTS (SELECT 1 FROM platform.tc_publication p WHERE p.claim_id = NEW.claim_id AND p.revision = NEW.revision
                               AND p.kind = 'PUBLISH') THEN
                    RAISE EXCEPTION 'a rollback goes only to a revision that was once published through the gate' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_publication_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.tc_publication
            FOR EACH ROW EXECUTE FUNCTION platform.tc_publication_guard();

        CREATE TABLE platform.tc_withdrawal (
            withdrawal_id uuid PRIMARY KEY,
            claim_id      uuid NOT NULL,
            revision      integer NOT NULL,
            actor         uuid NOT NULL,
            reason        text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 1000),
            at            timestamptz NOT NULL DEFAULT now(),
            review_due    timestamptz NOT NULL,
            reviewed_by   uuid,
            reviewed_at   timestamptz,
            review_note   text,
            FOREIGN KEY (claim_id, revision) REFERENCES platform.tc_claim_revision (claim_id, revision),
            CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL)),
            CHECK (reviewed_by IS NULL OR reviewed_by <> actor)
        );
        CREATE FUNCTION platform.tc_withdrawal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a withdrawal is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.withdrawal_id, NEW.claim_id, NEW.revision, NEW.actor, NEW.reason, NEW.at, NEW.review_due)
               IS DISTINCT FROM (OLD.withdrawal_id, OLD.claim_id, OLD.revision, OLD.actor, OLD.reason, OLD.at, OLD.review_due)
               OR OLD.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a withdrawal can only gain its one retrospective review' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_withdrawal_guard BEFORE UPDATE OR DELETE ON platform.tc_withdrawal
            FOR EACH ROW EXECUTE FUNCTION platform.tc_withdrawal_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.tc_withdrawal;
        DROP FUNCTION platform.tc_withdrawal_guard();
        DROP TABLE platform.tc_publication;
        DROP FUNCTION platform.tc_publication_guard();
        DROP TABLE platform.tc_approval;
        DROP FUNCTION platform.tc_approval_guard();
        DROP TABLE platform.tc_claim_revision;
        DROP FUNCTION platform.tc_claim_guard();
        DROP TABLE platform.tc_evidence;
        DROP FUNCTION platform.tc_evidence_guard();
    """)
