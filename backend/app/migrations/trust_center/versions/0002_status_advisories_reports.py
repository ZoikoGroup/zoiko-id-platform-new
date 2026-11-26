"""Measured availability, incident advisories and vulnerability reports (ZID-CP-WF12 v2.0, WF-12.06, 12.07, 12.08; CR-174).

Revision ID: trust_center_0002
Revises: trust_center_0001
Create Date: 2026-11-25

tc_sli_snapshot         one measured availability figure for one service, region and window: numerator, denominator, exclusions, where the raw count came from.
                        Recorded by one person and validated by another (the database refuses the same person twice). Facts never change. An empty window has no row,
                        so the public page can only ever say "not yet reported".
tc_advisory             a public advisory, tied to an incident in the operators' own registry (the service checks it exists through the operations port; there is no cross-module foreign key).
tc_advisory_version     its versions, oldest first. Append-only: a correction is a new version with a reason and the earlier one stays visible. A version is public only
                        once someone other than its author approved it (set once, never changed).
tc_report               a vulnerability report from outside. Contact details are optional and visible to staff only. The state moves forward; the text never changes.
tc_report_event         the report's append-only timeline: acknowledgements, triage, fixes, communications.
"""

from zid_platform.migrations import execute_script as _run

revision = "trust_center_0002"
down_revision = "trust_center_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.tc_sli_snapshot (
            snapshot_id   uuid PRIMARY KEY,
            service       text NOT NULL CHECK (service ~ '^[a-z][a-z0-9_-]{1,62}$'),
            region        text NOT NULL CHECK (region ~ '^[a-z0-9-]{2,24}$'),
            window_label  text NOT NULL CHECK (window_label ~ '^[0-9]{4}-[0-9]{2}(-[0-9]{2})?$'),
            numerator     bigint NOT NULL CHECK (numerator >= 0),
            denominator   bigint NOT NULL CHECK (denominator > 0),
            exclusions    text NOT NULL DEFAULT '',
            source_ref    text NOT NULL CHECK (length(btrim(source_ref)) >= 3),
            recorded_by   uuid NOT NULL,
            recorded_at   timestamptz NOT NULL DEFAULT now(),
            validated_by  uuid,
            validated_at  timestamptz,
            CHECK (numerator <= denominator),
            CHECK ((validated_by IS NULL) = (validated_at IS NULL)),
            CHECK (validated_by IS NULL OR validated_by <> recorded_by),
            UNIQUE (service, region, window_label)
        );
        CREATE FUNCTION platform.tc_sli_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a measured figure is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.snapshot_id, NEW.service, NEW.region, NEW.window_label, NEW.numerator, NEW.denominator, NEW.exclusions, NEW.source_ref, NEW.recorded_by, NEW.recorded_at)
               IS DISTINCT FROM
               (OLD.snapshot_id, OLD.service, OLD.region, OLD.window_label, OLD.numerator, OLD.denominator, OLD.exclusions, OLD.source_ref, OLD.recorded_by, OLD.recorded_at)
               OR OLD.validated_by IS NOT NULL THEN
                RAISE EXCEPTION 'a measured figure can only gain its one validation' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_sli_guard BEFORE UPDATE OR DELETE ON platform.tc_sli_snapshot
            FOR EACH ROW EXECUTE FUNCTION platform.tc_sli_guard();

        CREATE TABLE platform.tc_advisory (
            advisory_id  uuid PRIMARY KEY,
            incident_ref uuid NOT NULL,
            created_by   uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (incident_ref)
        );
        CREATE TABLE platform.tc_advisory_version (
            advisory_id  uuid NOT NULL REFERENCES platform.tc_advisory (advisory_id),
            seq          integer NOT NULL CHECK (seq >= 1),
            kind         text NOT NULL CHECK (kind IN ('PUBLISH', 'UPDATE', 'CORRECTION', 'RESOLVE', 'POSTMORTEM')),
            title        text NOT NULL CHECK (length(btrim(title)) BETWEEN 3 AND 200),
            body         text NOT NULL CHECK (length(btrim(body)) BETWEEN 3 AND 8000),
            reason       text NOT NULL DEFAULT '',
            author       uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            approved_by  uuid,
            approved_at  timestamptz,
            PRIMARY KEY (advisory_id, seq),
            CHECK ((seq = 1) = (kind = 'PUBLISH')),
            CHECK (kind <> 'CORRECTION' OR length(btrim(reason)) > 0),
            CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
            CHECK (approved_by IS NULL OR approved_by <> author)
        );
        CREATE FUNCTION platform.tc_advisory_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'an advisory version is never erased; publish a correction' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.advisory_id, NEW.seq, NEW.kind, NEW.title, NEW.body, NEW.reason, NEW.author, NEW.created_at)
               IS DISTINCT FROM (OLD.advisory_id, OLD.seq, OLD.kind, OLD.title, OLD.body, OLD.reason, OLD.author, OLD.created_at)
               OR OLD.approved_by IS NOT NULL THEN
                RAISE EXCEPTION 'an advisory version never changes; publish a correction' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_advisory_guard BEFORE UPDATE OR DELETE ON platform.tc_advisory_version
            FOR EACH ROW EXECUTE FUNCTION platform.tc_advisory_guard();

        CREATE TABLE platform.tc_report (
            report_id    uuid PRIMARY KEY,
            reference    text NOT NULL UNIQUE CHECK (reference ~ '^VR-[0-9A-Z]{10}$'),
            received_at  timestamptz NOT NULL DEFAULT now(),
            summary      text NOT NULL CHECK (length(summary) BETWEEN 20 AND 8000),
            affected     text NOT NULL DEFAULT '' CHECK (length(affected) <= 2000),
            severity     text NOT NULL DEFAULT 'medium' CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
            contact      text CHECK (contact IS NULL OR length(contact) <= 200),
            source_hash  text NOT NULL,
            state        text NOT NULL DEFAULT 'RECEIVED' CHECK (state IN ('RECEIVED', 'ACKNOWLEDGED', 'TRIAGED', 'FIXING', 'FIXED', 'DISCLOSED', 'CLOSED', 'OUT_OF_SCOPE')),
            assignee     uuid,
            ack_due      timestamptz NOT NULL,
            acknowledged_at timestamptz
        );
        CREATE INDEX tc_report_source ON platform.tc_report (source_hash, received_at);
        CREATE INDEX tc_report_open ON platform.tc_report (state, received_at);
        CREATE FUNCTION platform.tc_report_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a report is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.report_id, NEW.reference, NEW.received_at, NEW.summary, NEW.affected, NEW.contact, NEW.source_hash, NEW.ack_due)
               IS DISTINCT FROM (OLD.report_id, OLD.reference, OLD.received_at, OLD.summary, OLD.affected, OLD.contact, OLD.source_hash, OLD.ack_due) THEN
                RAISE EXCEPTION 'the text of a report never changes' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER tc_report_guard BEFORE UPDATE OR DELETE ON platform.tc_report
            FOR EACH ROW EXECUTE FUNCTION platform.tc_report_guard();

        CREATE TABLE platform.tc_report_event (
            report_id  uuid NOT NULL REFERENCES platform.tc_report (report_id),
            seq        integer NOT NULL,
            kind       text NOT NULL CHECK (kind IN ('RECEIVED', 'STATE', 'ASSIGNED', 'SEVERITY', 'COMMUNICATION', 'NOTE')),
            actor      uuid,
            note       text NOT NULL DEFAULT '' CHECK (length(note) <= 4000),
            at         timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (report_id, seq)
        );
        CREATE FUNCTION platform.tc_report_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the report timeline is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER tc_report_event_append_only BEFORE UPDATE OR DELETE ON platform.tc_report_event
            FOR EACH ROW EXECUTE FUNCTION platform.tc_report_event_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.tc_report_event;
        DROP FUNCTION platform.tc_report_event_guard();
        DROP TABLE platform.tc_report;
        DROP FUNCTION platform.tc_report_guard();
        DROP TABLE platform.tc_advisory_version;
        DROP FUNCTION platform.tc_advisory_guard();
        DROP TABLE platform.tc_advisory;
        DROP TABLE platform.tc_sli_snapshot;
        DROP FUNCTION platform.tc_sli_guard();
    """)
