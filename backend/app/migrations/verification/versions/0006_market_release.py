"""Market releases: the seven evidence gates, four independent approvals and the launch (ZID-RCG-003 RCG-G10, WF-08; CR-072).

Revision ID: verification_0006
Revises: verification_0005
Create Date: 2026-11-12

market_release: one immutable version of the release of one market (country, product, purpose). It pins the approved policy packs it covers and the supplier certifications it relies on.
Platform configuration, not tenant data: no tenant_id, no row-level security. market_release_gate: every gate result ever recorded, append-only, numbered in order by the database (no
sequence: the runtime role has no sequence privileges); the latest per gate counts. market_release_approval: who signed which release hash in which role, append-only. The database repeats the domain
rules so a caller that skips the domain code still cannot get around them: a release is frozen only with all seven gates PASSED and a pinned
pack; a frozen one never changes except along FROZEN, RELEASEABLE, LAUNCHED, WITHDRAWN; evidence is recorded only while it is a draft and never
by its author; an approval signs the stored hash and never comes from the author or from anyone who recorded evidence; a market has at most
one LAUNCHED release.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0006"
down_revision = "verification_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.market_release (
            release_id    text NOT NULL CHECK (length(release_id) BETWEEN 1 AND 128),
            version       integer NOT NULL CHECK (version >= 1),
            country       text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
            product       text NOT NULL CHECK (length(product) BETWEEN 1 AND 64),
            purpose       text NOT NULL CHECK (length(purpose) BETWEEN 1 AND 64),
            status        text NOT NULL CHECK (status IN ('DRAFT', 'FROZEN', 'RELEASEABLE', 'LAUNCHED', 'WITHDRAWN')),
            author        text NOT NULL CHECK (length(author) BETWEEN 1 AND 128),
            pack_refs     jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(pack_refs) = 'array'),
            supplier_scopes jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(supplier_scopes) = 'array'),
            release_hash  text CHECK (release_hash IS NULL OR release_hash ~ '^[0-9a-f]{64}$'),
            created_at    timestamptz NOT NULL,
            frozen_at     timestamptz,
            released_at   timestamptz,
            launched_at   timestamptz,
            withdrawn_at  timestamptz,
            PRIMARY KEY (release_id, version),
            CHECK ((status = 'DRAFT') = (release_hash IS NULL)),
            CHECK ((status = 'DRAFT') = (frozen_at IS NULL)),
            CHECK (status <> 'LAUNCHED' OR launched_at IS NOT NULL),
            CHECK (status <> 'WITHDRAWN' OR withdrawn_at IS NOT NULL)
        );
        CREATE UNIQUE INDEX market_release_one_live ON verify.market_release (country, product, purpose) WHERE status = 'LAUNCHED';
        CREATE INDEX market_release_market ON verify.market_release (country, product, purpose, version DESC);

        CREATE TABLE verify.market_release_gate (
            ord           integer NOT NULL,
            release_id    text NOT NULL,
            version       integer NOT NULL,
            gate          text NOT NULL CHECK (gate IN ('RG-01', 'RG-02', 'RG-03', 'RG-04', 'RG-05', 'RG-06', 'RG-07')),
            status        text NOT NULL CHECK (status IN ('PASSED', 'FAILED')),
            evidence_ref  text NOT NULL CHECK (evidence_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            role          text NOT NULL CHECK (role IN ('legal', 'regulatory_ops', 'security', 'privacy', 'qa', 'vendor_mgmt')),
            recorded_by   text NOT NULL CHECK (length(recorded_by) BETWEEN 1 AND 128),
            recorded_at   timestamptz NOT NULL,
            PRIMARY KEY (release_id, version, ord),
            FOREIGN KEY (release_id, version) REFERENCES verify.market_release (release_id, version)
        );
        CREATE INDEX market_release_gate_release ON verify.market_release_gate (release_id, version, gate, ord DESC);

        CREATE TABLE verify.market_release_approval (
            release_id    text NOT NULL,
            version       integer NOT NULL,
            role          text NOT NULL CHECK (role IN ('legal', 'security_grc', 'qa', 'product_owner')),
            approver      text NOT NULL CHECK (length(approver) BETWEEN 1 AND 128),
            release_hash  text NOT NULL CHECK (release_hash ~ '^[0-9a-f]{64}$'),
            approved_at   timestamptz NOT NULL,
            PRIMARY KEY (release_id, version, role),
            UNIQUE (release_id, version, approver),
            FOREIGN KEY (release_id, version) REFERENCES verify.market_release (release_id, version)
        );

        CREATE FUNCTION verify.market_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE passed integer;
        DECLARE approved integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'a frozen market release is never deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'DRAFT' THEN
                IF NEW.status NOT IN ('DRAFT', 'FROZEN') THEN
                    RAISE EXCEPTION 'a draft release is frozen before anything else' USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.status = 'FROZEN' THEN
                    SELECT count(*) INTO passed FROM (
                        SELECT DISTINCT ON (gate) gate, status FROM verify.market_release_gate
                        WHERE release_id = NEW.release_id AND version = NEW.version ORDER BY gate, ord DESC) latest
                    WHERE latest.status = 'PASSED';
                    IF passed <> 7 THEN
                        RAISE EXCEPTION 'a release is frozen only when all seven gates have passed' USING ERRCODE = 'check_violation';
                    END IF;
                    IF jsonb_array_length(NEW.pack_refs) = 0 THEN
                        RAISE EXCEPTION 'a release is frozen only with an approved policy pack pinned' USING ERRCODE = 'check_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.release_id, NEW.version, NEW.country, NEW.product, NEW.purpose, NEW.author, NEW.pack_refs, NEW.supplier_scopes, NEW.release_hash,
                NEW.created_at, NEW.frozen_at)
               IS DISTINCT FROM
               (OLD.release_id, OLD.version, OLD.country, OLD.product, OLD.purpose, OLD.author, OLD.pack_refs, OLD.supplier_scopes, OLD.release_hash,
                OLD.created_at, OLD.frozen_at) THEN
                RAISE EXCEPTION 'a frozen market release cannot be changed; make a new version' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'FROZEN' AND NEW.status = 'RELEASEABLE' THEN
                SELECT count(DISTINCT role) INTO approved FROM verify.market_release_approval a
                WHERE a.release_id = NEW.release_id AND a.version = NEW.version AND a.release_hash = NEW.release_hash;
                IF approved <> 4 THEN
                    RAISE EXCEPTION 'a release needs the four approvals of its hash before it is releaseable' USING ERRCODE = 'check_violation';
                END IF;
            ELSIF OLD.status = 'RELEASEABLE' AND NEW.status = 'LAUNCHED' THEN
                NULL;
            ELSIF OLD.status IN ('RELEASEABLE', 'LAUNCHED') AND NEW.status = 'WITHDRAWN' THEN
                NULL;
            ELSE
                RAISE EXCEPTION 'a market release moves only FROZEN, RELEASEABLE, LAUNCHED, WITHDRAWN' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER market_release_guard BEFORE UPDATE OR DELETE ON verify.market_release
            FOR EACH ROW EXECUTE FUNCTION verify.market_release_guard();

        CREATE FUNCTION verify.market_release_gate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r verify.market_release%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'market release gate results are append-only' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO r FROM verify.market_release WHERE release_id = NEW.release_id AND version = NEW.version;
            IF r.status <> 'DRAFT' THEN
                RAISE EXCEPTION 'evidence is recorded only while the release is a draft' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.recorded_by = r.author THEN
                RAISE EXCEPTION 'the author of a release records no evidence for it' USING ERRCODE = 'check_violation';
            END IF;
            SELECT coalesce(max(g.ord), 0) + 1 INTO NEW.ord FROM verify.market_release_gate g
            WHERE g.release_id = NEW.release_id AND g.version = NEW.version;
            RETURN NEW;
        END $$;
        CREATE TRIGGER market_release_gate_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.market_release_gate
            FOR EACH ROW EXECUTE FUNCTION verify.market_release_gate_guard();

        CREATE FUNCTION verify.market_release_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r verify.market_release%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'market release approvals are append-only' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO r FROM verify.market_release WHERE release_id = NEW.release_id AND version = NEW.version;
            IF r.status <> 'FROZEN' THEN
                RAISE EXCEPTION 'only a frozen release can be approved' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.approver = r.author THEN
                RAISE EXCEPTION 'the author of a release cannot approve it' USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM verify.market_release_gate g
                       WHERE g.release_id = NEW.release_id AND g.version = NEW.version AND g.recorded_by = NEW.approver) THEN
                RAISE EXCEPTION 'someone who recorded evidence for this release cannot approve it' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.release_hash <> r.release_hash THEN
                RAISE EXCEPTION 'an approval must sign the hash of this release' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER market_release_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.market_release_approval
            FOR EACH ROW EXECUTE FUNCTION verify.market_release_approval_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.market_release_approval, verify.market_release_gate, verify.market_release;
        DROP FUNCTION verify.market_release_approval_guard();
        DROP FUNCTION verify.market_release_gate_guard();
        DROP FUNCTION verify.market_release_guard();
    """)
