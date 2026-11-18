"""WF-05 verification cases and their evidence assertions (ZID-DTV-WF-05 §02, §06, §19, A09; CR-112).

Revision ID: verification_0022
Revises: verification_0021
Create Date: 2026-11-17

check_case: one row per case, bound to its tenant, an opaque subject reference, a purpose, a market, the processing region and the eligibility answer
it opened under (policy_hash, the requested and the available methods). The status moves only along the WF-05 states; CLOSED is final; the binding
never changes after the case is opened.

check_assertion: the evidence, one row per assertion, append-only. Each names its assertion kind, verdict, method and source. The table repeats the
rules that must hold even for a caller that skips the domain code: the method must be one the case was cleared for when it opened, a CLOSED case takes
no evidence, a non-PASS verdict names a reason, and no reason code says fraud.

Both tables are tenant data under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0022"
down_revision = "verification_0021"
branch_labels = None
depends_on = None

_METHODS = (
    "'EMAIL_CHALLENGE', 'PHONE_SMS_CHALLENGE', 'PHONE_VOICE_CHALLENGE', 'CARRIER_INTELLIGENCE', 'ADDRESS_LOOKUP', 'ADDRESS_DOCUMENT', "
    "'ADDRESS_DATA_SOURCE', 'ADDRESS_POSTAL_CHALLENGE', 'DEVICE_RISK'"
)
_RLS = """
        ALTER TABLE verify.check_case ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_case FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_case
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE verify.check_assertion ENABLE ROW LEVEL SECURITY;
        ALTER TABLE verify.check_assertion FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON verify.check_assertion
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run(
        f"""
        CREATE TABLE verify.check_case (
            case_id              uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            subject_ref          text NOT NULL CHECK (subject_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{2,127}}$' AND position('@' in subject_ref) = 0),
            purpose              text NOT NULL CHECK (purpose ~ '^[A-Z][A-Z0-9_]{{1,63}}$'),
            market               text NOT NULL CHECK (market ~ '^[A-Z]{{2}}$'),
            region               text NOT NULL CHECK (length(btrim(region)) BETWEEN 1 AND 64),
            requested_methods    text[] NOT NULL CHECK (cardinality(requested_methods) >= 1 AND requested_methods <@ ARRAY[{_METHODS}]::text[]),
            available_methods    text[] NOT NULL CHECK (available_methods <@ requested_methods),
            unavailable_reasons  text[] NOT NULL DEFAULT '{{}}',
            policy_hash          text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{{64}}$'),
            status               text NOT NULL CHECK (status IN ('CREATED', 'ELIGIBLE', 'IN_PROGRESS', 'REVIEW', 'COMPLETE', 'CLOSED')),
            correlation_id       text NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 128),
            created_by           text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 128),
            created_at           timestamptz NOT NULL,
            updated_at           timestamptz NOT NULL,
            CHECK (status = 'CLOSED' OR ((status = 'CREATED') = (cardinality(available_methods) = 0))),
            CHECK (updated_at >= created_at)
        );
        CREATE INDEX check_case_subject ON verify.check_case (tenant_id, subject_ref, created_at DESC);

        CREATE FUNCTION verify.check_case_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'cases are kept; erasure is a privacy workflow' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status NOT IN ('CREATED', 'ELIGIBLE') THEN
                    RAISE EXCEPTION 'a case opens CREATED or ELIGIBLE' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.case_id, NEW.tenant_id, NEW.subject_ref, NEW.purpose, NEW.market, NEW.region, NEW.requested_methods, NEW.available_methods,
                NEW.unavailable_reasons, NEW.policy_hash, NEW.correlation_id, NEW.created_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.case_id, OLD.tenant_id, OLD.subject_ref, OLD.purpose, OLD.market, OLD.region, OLD.requested_methods, OLD.available_methods,
                OLD.unavailable_reasons, OLD.policy_hash, OLD.correlation_id, OLD.created_by, OLD.created_at) THEN
                RAISE EXCEPTION 'a case keeps the binding it was opened with' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                (OLD.status = 'CREATED' AND NEW.status = 'CLOSED') OR
                (OLD.status = 'ELIGIBLE' AND NEW.status IN ('IN_PROGRESS', 'CLOSED')) OR
                (OLD.status = 'IN_PROGRESS' AND NEW.status IN ('REVIEW', 'COMPLETE', 'CLOSED')) OR
                (OLD.status = 'REVIEW' AND NEW.status IN ('IN_PROGRESS', 'COMPLETE', 'CLOSED')) OR
                (OLD.status = 'COMPLETE' AND NEW.status = 'CLOSED')
            ) THEN
                RAISE EXCEPTION 'a case cannot move from % to %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_case_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.check_case
            FOR EACH ROW EXECUTE FUNCTION verify.check_case_guard();

        CREATE TABLE verify.check_assertion (
            assertion_id   uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            case_id        uuid NOT NULL REFERENCES verify.check_case (case_id),
            assertion      text NOT NULL CHECK (assertion IN ('ADDRESS_FORMAT_VALID', 'ADDRESS_LOCATION_MATCH', 'ADDRESS_DOCUMENT_REVIEWED',
                                                              'ADDRESS_ASSOCIATION_CONFIRMED', 'CONTACT_CONTROL_CONFIRMED', 'DEVICE_RISK_OBSERVED')),
            verdict        text NOT NULL CHECK (verdict IN ('PASS', 'REVIEW', 'INDETERMINATE', 'FAIL', 'UNAVAILABLE')),
            method         text NOT NULL CHECK (method IN ({_METHODS})),
            source         text CHECK (source IS NULL OR source ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{{1,127}}$'),
            reason_codes   text[] NOT NULL DEFAULT '{{}}',
            confidence     text CHECK (confidence IS NULL OR confidence IN ('LOW', 'MEDIUM', 'HIGH')),
            observed_at    timestamptz NOT NULL,
            expires_at     timestamptz,
            recorded_by    text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at    timestamptz NOT NULL,
            CHECK (verdict NOT IN ('PASS', 'FAIL') OR source IS NOT NULL),
            CHECK (verdict = 'PASS' OR cardinality(reason_codes) >= 1),
            CHECK (NOT (array_to_string(reason_codes, ' ') ILIKE '%fraud%')),
            CHECK (expires_at IS NULL OR expires_at > observed_at)
        );
        CREATE INDEX check_assertion_case ON verify.check_assertion (tenant_id, case_id, recorded_at);

        CREATE FUNCTION verify.check_assertion_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            c verify.check_case%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'evidence assertions are append-only' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO c FROM verify.check_case WHERE case_id = NEW.case_id;
            IF NOT FOUND OR c.tenant_id <> NEW.tenant_id THEN
                RAISE EXCEPTION 'an assertion belongs to a case of the same tenant' USING ERRCODE = 'check_violation';
            END IF;
            IF c.status = 'CLOSED' THEN
                RAISE EXCEPTION 'a closed case takes no new evidence' USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (NEW.method = ANY (c.available_methods)) THEN
                RAISE EXCEPTION 'the method % was not available when the case opened', NEW.method USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER check_assertion_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.check_assertion
            FOR EACH ROW EXECUTE FUNCTION verify.check_assertion_guard();
    """
        + _RLS
    )


def downgrade() -> None:
    _run("""
        DROP TABLE verify.check_assertion;
        DROP FUNCTION verify.check_assertion_guard();
        DROP TABLE verify.check_case;
        DROP FUNCTION verify.check_case_guard();
    """)
