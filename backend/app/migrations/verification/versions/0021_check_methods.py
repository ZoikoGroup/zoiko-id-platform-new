"""WF-05 check method lifecycle and kill switch (ZID-DTV-WF-05 §04, §05, §27; CR-112).

Revision ID: verification_0021
Revises: verification_0020
Create Date: 2026-11-17

check_method_state and check_method_proposal: the same lifecycle as the biometric methods (verification_0019 and verification_0020), for the WF-05 check
methods (email, SMS and voice challenges, carrier intelligence, address lookup, document, data source and postal code, device risk). One row per version
of an exact scope (method, adapter engine id and version, country, purpose, optionally one tenant); append-only; a step up is two people and one step
at a time; a pilot names a tenant; a live state carries its DPIA and evaluation (certification) references; PRODUCTION_ELIGIBLE carries an exposure
percentage, and an approved step up its decision hash.

Platform configuration, not tenant data: no row-level security (tenant_id names the pilot tenant, it does not own the row).
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0021"
down_revision = "verification_0020"
branch_labels = None
depends_on = None

_METHODS = (
    "'EMAIL_CHALLENGE', 'PHONE_SMS_CHALLENGE', 'PHONE_VOICE_CHALLENGE', 'CARRIER_INTELLIGENCE', 'ADDRESS_LOOKUP', 'ADDRESS_DOCUMENT', "
    "'ADDRESS_DATA_SOURCE', 'ADDRESS_POSTAL_CHALLENGE', 'DEVICE_RISK'"
)
_STATES = "'OFF', 'PAUSED', 'LAB_ONLY', 'PILOT_GATED', 'PRODUCTION_ELIGIBLE'"
_REF = "'^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE verify.check_method_proposal (
            proposal_id      uuid PRIMARY KEY,
            scope_id         text NOT NULL CHECK (scope_id ~ '^[a-z0-9][a-z0-9-]{{0,127}}$'),
            method           text NOT NULL CHECK (method IN ({_METHODS})),
            engine_id        text NOT NULL CHECK (engine_id ~ '^[a-z0-9][a-z0-9._-]{{1,63}}$'),
            engine_version   text NOT NULL CHECK (length(btrim(engine_version)) BETWEEN 1 AND 64),
            country          text NOT NULL CHECK (country ~ '^[A-Z]{{2}}$'),
            purpose          text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z0-9_]{{1,63}}$'),
            tenant_id        uuid,
            from_version     integer NOT NULL CHECK (from_version >= 0),
            target           text NOT NULL CHECK (target IN ('LAB_ONLY', 'PILOT_GATED', 'PRODUCTION_ELIGIBLE')),
            dpia_ref         text CHECK (dpia_ref IS NULL OR dpia_ref ~ {_REF}),
            evaluation_ref   text CHECK (evaluation_ref IS NULL OR evaluation_ref ~ {_REF}),
            reason           text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 1000),
            evidence_ref     text NOT NULL CHECK (evidence_ref ~ {_REF}),
            proposed_by      text NOT NULL CHECK (length(btrim(proposed_by)) BETWEEN 1 AND 128),
            proposed_at      timestamptz NOT NULL,
            status           text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'APPROVED', 'REJECTED')),
            decided_by       text,
            decided_at       timestamptz,
            decision_reason  text CHECK (decision_reason IS NULL OR length(btrim(decision_reason)) BETWEEN 10 AND 1000),
            exposure_percent smallint NOT NULL DEFAULT 100 CHECK (exposure_percent BETWEEN 1 AND 100),
            CHECK (target <> 'PILOT_GATED' OR tenant_id IS NOT NULL),
            CHECK (target = 'LAB_ONLY' OR (dpia_ref IS NOT NULL AND evaluation_ref IS NOT NULL)),
            CHECK ((status = 'OPEN') = (decided_by IS NULL AND decided_at IS NULL AND decision_reason IS NULL)),
            CHECK (status <> 'APPROVED' OR decided_by <> proposed_by),
            CONSTRAINT check_method_proposal_exposure CHECK (target = 'PRODUCTION_ELIGIBLE' OR exposure_percent = 100)
        );
        CREATE UNIQUE INDEX check_method_proposal_one_open
            ON verify.check_method_proposal (scope_id) WHERE status = 'OPEN';

        CREATE FUNCTION verify.check_method_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'proposals are kept' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'OPEN' THEN
                    RAISE EXCEPTION 'a new proposal is OPEN' USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.proposed_at > now() + interval '5 minutes' THEN
                    RAISE EXCEPTION 'a proposal cannot be made in the future' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'OPEN' THEN
                RAISE EXCEPTION 'a proposal is decided once' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.proposal_id, NEW.scope_id, NEW.method, NEW.engine_id, NEW.engine_version, NEW.country, NEW.purpose, NEW.tenant_id,
                NEW.from_version, NEW.target, NEW.dpia_ref, NEW.evaluation_ref, NEW.reason, NEW.evidence_ref, NEW.proposed_by, NEW.proposed_at,
                NEW.exposure_percent)
               IS DISTINCT FROM
               (OLD.proposal_id, OLD.scope_id, OLD.method, OLD.engine_id, OLD.engine_version, OLD.country, OLD.purpose, OLD.tenant_id,
                OLD.from_version, OLD.target, OLD.dpia_ref, OLD.evaluation_ref, OLD.reason, OLD.evidence_ref, OLD.proposed_by, OLD.proposed_at,
                OLD.exposure_percent) THEN
                RAISE EXCEPTION 'a proposal is not edited; only its decision is recorded' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_method_proposal_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.check_method_proposal
            FOR EACH ROW EXECUTE FUNCTION verify.check_method_proposal_guard();

        CREATE TABLE verify.check_method_state (
            scope_id         text NOT NULL CHECK (scope_id ~ '^[a-z0-9][a-z0-9-]{{0,127}}$'),
            version          integer NOT NULL CHECK (version >= 1),
            method           text NOT NULL CHECK (method IN ({_METHODS})),
            engine_id        text NOT NULL CHECK (engine_id ~ '^[a-z0-9][a-z0-9._-]{{1,63}}$'),
            engine_version   text NOT NULL CHECK (length(btrim(engine_version)) BETWEEN 1 AND 64),
            country          text NOT NULL CHECK (country ~ '^[A-Z]{{2}}$'),
            purpose          text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z0-9_]{{1,63}}$'),
            tenant_id        uuid,
            state            text NOT NULL CHECK (state IN ({_STATES})),
            dpia_ref         text CHECK (dpia_ref IS NULL OR dpia_ref ~ {_REF}),
            evaluation_ref   text CHECK (evaluation_ref IS NULL OR evaluation_ref ~ {_REF}),
            reason           text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 1000),
            evidence_ref     text NOT NULL CHECK (evidence_ref ~ {_REF}),
            recorded_by      text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            proposed_by      text,
            proposal_id      uuid REFERENCES verify.check_method_proposal (proposal_id),
            effective_at     timestamptz NOT NULL,
            recorded_at      timestamptz NOT NULL,
            exposure_percent smallint NOT NULL DEFAULT 100 CHECK (exposure_percent BETWEEN 1 AND 100),
            decision_hash    text CHECK (decision_hash IS NULL OR decision_hash ~ '^[0-9a-f]{{64}}$'),
            PRIMARY KEY (scope_id, version),
            CHECK (state <> 'PILOT_GATED' OR tenant_id IS NOT NULL),
            CHECK (state NOT IN ('PILOT_GATED', 'PRODUCTION_ELIGIBLE') OR (dpia_ref IS NOT NULL AND evaluation_ref IS NOT NULL)),
            CHECK ((proposal_id IS NULL) = (proposed_by IS NULL)),
            CHECK (proposed_by IS NULL OR proposed_by <> recorded_by),
            CONSTRAINT check_method_state_exposure CHECK (state = 'PRODUCTION_ELIGIBLE' OR exposure_percent = 100),
            CONSTRAINT check_method_state_signed CHECK (proposal_id IS NULL OR decision_hash IS NOT NULL)
        );
        -- one scope id per exact scope, so a tenant's scope and the all-tenant one are always two ids
        CREATE UNIQUE INDEX check_method_state_scope
            ON verify.check_method_state (method, engine_id, engine_version, country, purpose, COALESCE(tenant_id::text, '*'), version);
        CREATE INDEX check_method_state_lookup
            ON verify.check_method_state (method, engine_id, engine_version, country, purpose);

        CREATE FUNCTION verify.check_method_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            last verify.check_method_state%ROWTYPE;
            last_rank integer;
            new_rank integer;
            other text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'check method states are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'a state cannot take effect in the future' USING ERRCODE = 'check_violation';
            END IF;
            SELECT scope_id INTO other FROM verify.check_method_state
             WHERE scope_id <> NEW.scope_id AND method = NEW.method AND engine_id = NEW.engine_id AND engine_version = NEW.engine_version
               AND country = NEW.country AND purpose = NEW.purpose AND tenant_id IS NOT DISTINCT FROM NEW.tenant_id
             LIMIT 1;
            IF FOUND THEN
                RAISE EXCEPTION 'this scope already has the id %', other USING ERRCODE = 'check_violation';
            END IF;
            new_rank := CASE NEW.state WHEN 'LAB_ONLY' THEN 1 WHEN 'PILOT_GATED' THEN 2 WHEN 'PRODUCTION_ELIGIBLE' THEN 3 ELSE 0 END;
            SELECT * INTO last FROM verify.check_method_state
             WHERE scope_id = NEW.scope_id ORDER BY version DESC LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.version <> 1 OR NEW.state <> 'LAB_ONLY' OR NEW.proposal_id IS NULL THEN
                    RAISE EXCEPTION 'a scope starts at version 1, LAB_ONLY, from an approved proposal' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.version <> last.version + 1 THEN
                RAISE EXCEPTION 'versions follow each other' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.method, NEW.engine_id, NEW.engine_version, NEW.country, NEW.purpose, NEW.tenant_id)
               IS DISTINCT FROM (last.method, last.engine_id, last.engine_version, last.country, last.purpose, last.tenant_id) THEN
                RAISE EXCEPTION 'a scope id always names the same scope' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at < last.effective_at THEN
                RAISE EXCEPTION 'a state cannot take effect before the one it follows' USING ERRCODE = 'check_violation';
            END IF;
            last_rank := CASE last.state WHEN 'LAB_ONLY' THEN 1 WHEN 'PILOT_GATED' THEN 2 WHEN 'PRODUCTION_ELIGIBLE' THEN 3 ELSE 0 END;
            IF new_rank > last_rank THEN
                IF NEW.proposal_id IS NULL OR new_rank <> last_rank + 1 THEN
                    RAISE EXCEPTION 'a step up is one step, from an approved proposal' USING ERRCODE = 'check_violation';
                END IF;
            ELSIF NEW.state = 'PRODUCTION_ELIGIBLE' AND last.state = 'PRODUCTION_ELIGIBLE' THEN
                IF NEW.proposal_id IS NOT NULL AND NEW.exposure_percent <= last.exposure_percent THEN
                    RAISE EXCEPTION 'a proposal within production only widens the exposure' USING ERRCODE = 'check_violation';
                ELSIF NEW.proposal_id IS NULL AND NEW.exposure_percent >= last.exposure_percent THEN
                    RAISE EXCEPTION 'a step down within production narrows the exposure' USING ERRCODE = 'check_violation';
                END IF;
            ELSIF NEW.proposal_id IS NOT NULL THEN
                RAISE EXCEPTION 'a proposal only steps up' USING ERRCODE = 'check_violation';
            ELSIF NEW.state = last.state THEN
                RAISE EXCEPTION 'the scope is already %', last.state USING ERRCODE = 'check_violation';
            ELSIF last.state = 'PAUSED' AND NEW.state <> 'OFF' THEN
                RAISE EXCEPTION 'a paused scope is resumed by a new approval, or retired' USING ERRCODE = 'check_violation';
            ELSIF last.state = 'OFF' THEN
                RAISE EXCEPTION 'nothing to step down from OFF' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_method_state_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.check_method_state
            FOR EACH ROW EXECUTE FUNCTION verify.check_method_state_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_method_state;
        DROP FUNCTION verify.check_method_state_guard();
        DROP TABLE verify.check_method_proposal;
        DROP FUNCTION verify.check_method_proposal_guard();
    """)
