"""Biometric method exposure and the signed release decision (ZID-DTV-WF-04 B11; CR-110).

Revision ID: verification_0020
Revises: verification_0019
Create Date: 2026-11-17

PRODUCTION_ELIGIBLE carries the share of people it runs for (`exposure_percent`, 1 to 100; every other state is 100). Widening it is an approved
proposal; narrowing it is a step down by one person. An approved step up carries `decision_hash`, the digest of exactly what was approved. The guard
learns the two moves within production; everything else it refused before it still refuses.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0020"
down_revision = "verification_0019"
branch_labels = None
depends_on = None

_GUARD_V2 = """
        CREATE OR REPLACE FUNCTION verify.biometric_method_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            last verify.biometric_method_state%ROWTYPE;
            last_rank integer;
            new_rank integer;
            other text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'biometric method states are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'a state cannot take effect in the future' USING ERRCODE = 'check_violation';
            END IF;
            SELECT scope_id INTO other FROM verify.biometric_method_state
             WHERE scope_id <> NEW.scope_id AND method = NEW.method AND engine_id = NEW.engine_id AND engine_version = NEW.engine_version
               AND country = NEW.country AND purpose = NEW.purpose AND tenant_id IS NOT DISTINCT FROM NEW.tenant_id
             LIMIT 1;
            IF FOUND THEN
                RAISE EXCEPTION 'this scope already has the id %', other USING ERRCODE = 'check_violation';
            END IF;
            new_rank := CASE NEW.state WHEN 'LAB_ONLY' THEN 1 WHEN 'PILOT_GATED' THEN 2 WHEN 'PRODUCTION_ELIGIBLE' THEN 3 ELSE 0 END;
            SELECT * INTO last FROM verify.biometric_method_state
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
"""

_GUARD_V1 = """
        CREATE OR REPLACE FUNCTION verify.biometric_method_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            last verify.biometric_method_state%ROWTYPE;
            last_rank integer;
            new_rank integer;
            other text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'biometric method states are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'a state cannot take effect in the future' USING ERRCODE = 'check_violation';
            END IF;
            SELECT scope_id INTO other FROM verify.biometric_method_state
             WHERE scope_id <> NEW.scope_id AND method = NEW.method AND engine_id = NEW.engine_id AND engine_version = NEW.engine_version
               AND country = NEW.country AND purpose = NEW.purpose AND tenant_id IS NOT DISTINCT FROM NEW.tenant_id
             LIMIT 1;
            IF FOUND THEN
                RAISE EXCEPTION 'this scope already has the id %', other USING ERRCODE = 'check_violation';
            END IF;
            new_rank := CASE NEW.state WHEN 'LAB_ONLY' THEN 1 WHEN 'PILOT_GATED' THEN 2 WHEN 'PRODUCTION_ELIGIBLE' THEN 3 ELSE 0 END;
            SELECT * INTO last FROM verify.biometric_method_state
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
"""


def upgrade() -> None:
    _run(
        """
        ALTER TABLE verify.biometric_method_proposal
            ADD COLUMN exposure_percent smallint NOT NULL DEFAULT 100 CHECK (exposure_percent BETWEEN 1 AND 100),
            ADD CONSTRAINT biometric_method_proposal_exposure CHECK (target = 'PRODUCTION_ELIGIBLE' OR exposure_percent = 100);
        ALTER TABLE verify.biometric_method_state
            ADD COLUMN exposure_percent smallint NOT NULL DEFAULT 100 CHECK (exposure_percent BETWEEN 1 AND 100),
            ADD COLUMN decision_hash text CHECK (decision_hash IS NULL OR decision_hash ~ '^[0-9a-f]{64}$'),
            ADD CONSTRAINT biometric_method_state_exposure CHECK (state = 'PRODUCTION_ELIGIBLE' OR exposure_percent = 100);
        ALTER TABLE verify.biometric_method_state
            ADD CONSTRAINT biometric_method_state_signed CHECK (proposal_id IS NULL OR decision_hash IS NOT NULL) NOT VALID;
    """
        + _GUARD_V2
    )


def downgrade() -> None:
    _run(
        _GUARD_V1
        + """
        ALTER TABLE verify.biometric_method_state
            DROP CONSTRAINT biometric_method_state_signed,
            DROP CONSTRAINT biometric_method_state_exposure,
            DROP COLUMN decision_hash,
            DROP COLUMN exposure_percent;
        ALTER TABLE verify.biometric_method_proposal
            DROP CONSTRAINT biometric_method_proposal_exposure,
            DROP COLUMN exposure_percent;
    """
    )
