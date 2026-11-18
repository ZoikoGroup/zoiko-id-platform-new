"""A representative's mandate and its second signatory (ZID-DTV-WF-02 K05; CR-109).

Revision ID: organization_0011
Revises: organization_0010
Create Date: 2026-11-17

kyb_mandate: who may act for a company, for which binding acts, between which dates, on what evidence. The person who recorded it is never the person it names (the table repeats the rule).
The only change a row ever takes is its revocation, once, with a reason; nothing is deleted. kyb_mandate_cosign: the second signatory, a different person from the representative and from the
recorder (the table repeats that too). Row-level security by company on both.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0011"
down_revision = "organization_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_mandate (
            mandate_id    uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            person        uuid NOT NULL,
            recorded_by   uuid NOT NULL CHECK (recorded_by <> person),
            role          text NOT NULL CHECK (role IN ('DIRECTOR', 'SECRETARY', 'EMPLOYEE', 'AGENT', 'TRUSTEE', 'PARTNER')),
            source        text NOT NULL CHECK (source IN ('REGISTRY_OFFICER', 'BOARD_MINUTE', 'POWER_OF_ATTORNEY', 'AUTHORIZED_MANDATE', 'OTHER')),
            evidence_ref  text NOT NULL CHECK (length(evidence_ref) BETWEEN 1 AND 512),
            powers        text[] NOT NULL CHECK (cardinality(powers) >= 1),
            second_signatory_required boolean NOT NULL,
            valid_from    timestamptz NOT NULL,
            valid_to      timestamptz CHECK (valid_to IS NULL OR valid_to > valid_from),
            created_at    timestamptz NOT NULL DEFAULT now(),
            revoked_at    timestamptz,
            revoked_by    uuid,
            revoked_reason text CHECK (revoked_reason IS NULL OR length(revoked_reason) BETWEEN 10 AND 2000),
            CHECK ((revoked_at IS NULL) = (revoked_by IS NULL) AND (revoked_at IS NULL) = (revoked_reason IS NULL))
        );
        CREATE INDEX kyb_mandate_by_company ON org.kyb_mandate (tenant_id, person);

        CREATE FUNCTION org.kyb_mandate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a mandate is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a revoked mandate never changes' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.mandate_id <> OLD.mandate_id OR NEW.tenant_id <> OLD.tenant_id OR NEW.person <> OLD.person OR NEW.recorded_by <> OLD.recorded_by
               OR NEW.role <> OLD.role OR NEW.source <> OLD.source OR NEW.evidence_ref <> OLD.evidence_ref OR NEW.powers <> OLD.powers
               OR NEW.second_signatory_required <> OLD.second_signatory_required OR NEW.valid_from <> OLD.valid_from
               OR NEW.valid_to IS DISTINCT FROM OLD.valid_to OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'a mandate changes only by being revoked' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER kyb_mandate_guard BEFORE UPDATE OR DELETE ON org.kyb_mandate
            FOR EACH ROW EXECUTE FUNCTION org.kyb_mandate_guard();

        CREATE TABLE org.kyb_mandate_cosign (
            cosign_id   uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            mandate_id  uuid NOT NULL REFERENCES org.kyb_mandate (mandate_id),
            cosigner    uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (mandate_id, cosigner)
        );

        CREATE FUNCTION org.kyb_mandate_cosign_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE m org.kyb_mandate%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'a second signature never changes and is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO m FROM org.kyb_mandate WHERE mandate_id = NEW.mandate_id;
            IF NEW.cosigner = m.person THEN
                RAISE EXCEPTION 'the representative cannot be their own second signatory' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.cosigner = m.recorded_by THEN
                RAISE EXCEPTION 'the person who recorded the mandate cannot be its second signatory' USING ERRCODE = 'check_violation';
            END IF;
            IF m.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a revoked mandate takes no second signature' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER kyb_mandate_cosign_guard BEFORE INSERT OR UPDATE OR DELETE ON org.kyb_mandate_cosign
            FOR EACH ROW EXECUTE FUNCTION org.kyb_mandate_cosign_guard();

        ALTER TABLE org.kyb_mandate ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.kyb_mandate FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.kyb_mandate
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE org.kyb_mandate_cosign ENABLE ROW LEVEL SECURITY;
        ALTER TABLE org.kyb_mandate_cosign FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON org.kyb_mandate_cosign
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.kyb_mandate_cosign;
        DROP FUNCTION org.kyb_mandate_cosign_guard();
        DROP TABLE org.kyb_mandate;
        DROP FUNCTION org.kyb_mandate_guard();
    """)
