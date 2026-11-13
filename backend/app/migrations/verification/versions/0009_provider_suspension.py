"""Exact-scope provider suspensions: the kill switch and its rollback (ZID-RCG-003 RCG-G08, section 08.2; CR-078).

Revision ID: verification_0009
Revises: verification_0008
Create Date: 2026-11-12

provider_suspension: one row per version of a suspension. Version 1 applies it (ACTIVE); the next version lifts it (LIFTED). Append-only, so a past
moment is always answered by the version in force then. Platform configuration, not tenant data: no tenant_id, no row-level security. The table repeats
the domain rules: no update or delete; version 1 is ACTIVE; a provider-wide suspension only for a breach; a lift follows an ACTIVE version, keeps the
scope, is by another person, and takes effect no earlier than the suspension; nothing takes effect in the future.
"""

from zid_platform.migrations import execute_script as _run

revision = "verification_0009"
down_revision = "verification_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE verify.provider_suspension (
            suspension_id    text NOT NULL CHECK (suspension_id ~ '^[a-z0-9][a-z0-9-]{0,127}$'),
            version          integer NOT NULL CHECK (version >= 1),
            provider         text NOT NULL CHECK (length(btrim(provider)) BETWEEN 1 AND 128),
            service_id       text CHECK (service_id IS NULL OR length(service_id) BETWEEN 1 AND 64),
            scheme           text CHECK (scheme IS NULL OR length(scheme) BETWEEN 1 AND 64),
            country          text CHECK (country IS NULL OR country ~ '^[A-Z]{2}$'),
            subdivision      text CHECK (subdivision IS NULL OR length(subdivision) BETWEEN 1 AND 16),
            purpose          text CHECK (purpose IS NULL OR length(purpose) BETWEEN 1 AND 64),
            profession       text CHECK (profession IS NULL OR length(profession) BETWEEN 1 AND 64),
            credential_class text CHECK (credential_class IS NULL OR length(credential_class) BETWEEN 1 AND 64),
            trigger          text NOT NULL CHECK (trigger IN ('CERTIFICATION_WITHDRAWN', 'PROVIDER_BREACH', 'ENTITLEMENT_WITHDRAWN',
                                                              'TRUST_LIST_STALE', 'LICENSE_SUSPENDED', 'MANUAL')),
            status           text NOT NULL CHECK (status IN ('ACTIVE', 'LIFTED')),
            reason           text NOT NULL CHECK (length(btrim(reason)) BETWEEN 10 AND 1000),
            evidence_ref     text NOT NULL CHECK (evidence_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/#@+=-]{2,255}$'),
            effective_at     timestamptz NOT NULL,
            recorded_by      text NOT NULL CHECK (length(btrim(recorded_by)) BETWEEN 1 AND 128),
            recorded_at      timestamptz NOT NULL,
            PRIMARY KEY (suspension_id, version),
            CHECK (trigger = 'PROVIDER_BREACH' OR num_nonnulls(service_id, scheme, country, subdivision, purpose, profession, credential_class) > 0)
        );
        CREATE INDEX provider_suspension_provider ON verify.provider_suspension (provider, effective_at);

        CREATE FUNCTION verify.provider_suspension_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            last verify.provider_suspension%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'provider suspensions are append-only' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at > now() + interval '5 minutes' THEN
                RAISE EXCEPTION 'a suspension cannot take effect in the future' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO last FROM verify.provider_suspension
             WHERE suspension_id = NEW.suspension_id ORDER BY version DESC LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.version <> 1 OR NEW.status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'a new suspension is version 1 and ACTIVE' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF last.status <> 'ACTIVE' OR NEW.status <> 'LIFTED' OR NEW.version <> last.version + 1 THEN
                RAISE EXCEPTION 'only an ACTIVE suspension is lifted, by the next version' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.provider, NEW.service_id, NEW.scheme, NEW.country, NEW.subdivision, NEW.purpose, NEW.profession, NEW.credential_class, NEW.trigger)
               IS DISTINCT FROM
               (last.provider, last.service_id, last.scheme, last.country, last.subdivision, last.purpose, last.profession, last.credential_class, last.trigger) THEN
                RAISE EXCEPTION 'a lift keeps the scope of the suspension' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.recorded_by = last.recorded_by THEN
                RAISE EXCEPTION 'a different person lifts a suspension' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.effective_at < last.effective_at THEN
                RAISE EXCEPTION 'a lift cannot take effect before the suspension' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_suspension_guard BEFORE INSERT OR UPDATE OR DELETE ON verify.provider_suspension
            FOR EACH ROW EXECUTE FUNCTION verify.provider_suspension_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE verify.provider_suspension;
        DROP FUNCTION verify.provider_suspension_guard();
    """)
