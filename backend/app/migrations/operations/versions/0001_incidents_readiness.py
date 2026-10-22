"""Incidents, post-incident action items, production readiness reviews and change freezes
(ZID-SRE-001 §15-17, §23-24, §26; G-W8; CR-017).

Revision ID: operations_0001
Revises:
Create Date: 2026-10-22

Operator-plane operational records, not tenant content, so not tenant-scoped. Free text is the
operators' own (titles, notes); customer data never belongs here - incidents reference tenants,
operations and evidence by id only.
- incident / incident_entry: the incident and its append-only, timestamped timeline (declaration,
  severity changes, role assignments, actions, decisions, resolution, closure).
- incident_action_item: post-incident review actions (owner, due date, independent verification).
- readiness_review / readiness_gate: one PRR of a service with its §24 gate results.
- change_freeze: an explicit change freeze of a service (one active freeze per service).
"""

from zid_platform.migrations import execute_script as _run

revision = "operations_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE platform.incident (
            incident_id        uuid PRIMARY KEY,
            title              text NOT NULL CHECK (length(title) BETWEEN 4 AND 200),
            severity           text NOT NULL CHECK (severity IN ('SEV0', 'SEV1', 'SEV2', 'SEV3', 'SEV4')),
            status             text NOT NULL DEFAULT 'DECLARED' CHECK (status IN
                               ('DECLARED', 'MITIGATING', 'RECOVERY_VALIDATION', 'RESOLVED', 'CLOSED')),
            security           boolean NOT NULL DEFAULT false,
            services           text[] NOT NULL DEFAULT '{}',
            cells              text[] NOT NULL DEFAULT '{}',
            roles              jsonb NOT NULL DEFAULT '{}',
            declared_by        uuid NOT NULL,
            declared_at        timestamptz NOT NULL DEFAULT now(),
            mitigated_at       timestamptz,
            resolved_at        timestamptz,
            closed_at          timestamptz,
            pir_ref            text,
            version            integer NOT NULL DEFAULT 1,
            CHECK (severity <> 'SEV0' OR security)
        );
        CREATE INDEX incident_open ON platform.incident (status, declared_at DESC);

        CREATE TABLE platform.incident_entry (
            incident_id   uuid NOT NULL REFERENCES platform.incident (incident_id),
            seq           integer NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('DECLARED', 'SEVERITY', 'STATUS', 'ROLE', 'ACTION',
                          'DECISION', 'COMMUNICATION', 'RESOLVED', 'CLOSED')),
            actor         uuid NOT NULL,
            note          text NOT NULL CHECK (length(note) <= 4000),
            data          jsonb NOT NULL DEFAULT '{}',
            at            timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (incident_id, seq)
        );
        CREATE FUNCTION platform.incident_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'the incident timeline is append-only' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER incident_entry_append_only BEFORE UPDATE OR DELETE ON platform.incident_entry
            FOR EACH ROW EXECUTE FUNCTION platform.incident_entry_guard();

        CREATE TABLE platform.incident_action_item (
            item_id       uuid PRIMARY KEY,
            incident_id   uuid NOT NULL REFERENCES platform.incident (incident_id),
            title         text NOT NULL CHECK (length(title) BETWEEN 4 AND 300),
            owner         uuid,
            due           date,
            verified_by   uuid,
            verified_at   timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (verified_by IS NULL OR verified_by IS DISTINCT FROM owner)
        );

        CREATE TABLE platform.readiness_review (
            review_id          uuid PRIMARY KEY,
            service_id         text NOT NULL CHECK (service_id ~ '^[a-z][a-z0-9-]{1,62}$'),
            release_ref        text NOT NULL,
            production_ready   boolean NOT NULL,
            recorded_by        uuid NOT NULL,
            recorded_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX readiness_review_service ON platform.readiness_review (service_id, recorded_at DESC);

        CREATE TABLE platform.readiness_gate (
            review_id             uuid NOT NULL REFERENCES platform.readiness_review (review_id),
            gate                  text NOT NULL,
            status                text NOT NULL CHECK (status IN ('PASS', 'FAIL', 'ACCEPTED')),
            evidence_ref          text,
            risk_owner            uuid,
            risk_expires          date,
            compensating_control  text,
            PRIMARY KEY (review_id, gate),
            CHECK (status <> 'ACCEPTED' OR (risk_owner IS NOT NULL AND risk_expires IS NOT NULL
                   AND compensating_control IS NOT NULL))
        );

        CREATE TABLE platform.change_freeze (
            freeze_id     uuid PRIMARY KEY,
            service_id    text NOT NULL CHECK (service_id ~ '^[a-z][a-z0-9-]{1,62}$'),
            reason_code   text NOT NULL,
            incident_id   uuid REFERENCES platform.incident (incident_id),
            frozen_by     uuid NOT NULL,
            frozen_at     timestamptz NOT NULL DEFAULT now(),
            lifted_by     uuid,
            lifted_at     timestamptz,
            lift_grant_id uuid,
            CHECK ((lifted_by IS NULL) = (lifted_at IS NULL))
        );
        CREATE UNIQUE INDEX change_freeze_one_active ON platform.change_freeze (service_id)
            WHERE lifted_at IS NULL;
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.change_freeze;
        DROP TABLE platform.readiness_gate;
        DROP TABLE platform.readiness_review;
        DROP TABLE platform.incident_action_item;
        DROP TABLE platform.incident_entry;
        DROP FUNCTION platform.incident_entry_guard();
        DROP TABLE platform.incident;
    """)
