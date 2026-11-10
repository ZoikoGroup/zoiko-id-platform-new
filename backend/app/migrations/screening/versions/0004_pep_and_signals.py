"""PEP as a list kind, and fraud/adverse-media signals with their review items (CR-068; ZID-GAP-001 WP-14).

Revision ID: screening_0004
Revises: screening_0003
Create Date: 2026-11-10

sanction_list_version: the list_id check also allows 'PEP' (a politically exposed persons list goes through the same version,
freshness and matching machinery as a sanctions list).
screening_velocity_event: one row per attempt (sign-up, verification, claim), keyed by hashes only (device, contact, network). No raw
email, phone or address is stored. Rows are counted inside a sliding window and may be pruned after the longest window.
screening_signal: a signal a rule raised (a velocity breach or an adverse-media result): the rule, the counts or the provider, the
query time and the rationale. Append-only.
screening_signal_review: the review item a signal opens. One open item per (tenant, dedupe key), so a repeat does not duplicate. A
person resolves it with an outcome, a rationale and an evidence reference; the signal row is never changed or deleted.
"""

from zid_platform.migrations import execute_script as _run

revision = "screening_0004"
down_revision = "screening_0003"
branch_labels = None
depends_on = None

TABLES = ("screening_velocity_event", "screening_signal", "screening_signal_review")
_RLS = """
    ALTER TABLE verify.{t} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE verify.{t} FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON verify.{t}
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
"""


def upgrade() -> None:
    _run("""
        ALTER TABLE verify.sanction_list_version DROP CONSTRAINT sanction_list_version_list_id_check;
        ALTER TABLE verify.sanction_list_version ADD CONSTRAINT sanction_list_version_list_id_check
            CHECK (list_id IN ('OFAC_SDN', 'OFAC_NON_SDN', 'UK_SANCTIONS', 'EU_CONSOLIDATED', 'UN_SC', 'LOCAL', 'PEP'));

        CREATE TABLE verify.screening_velocity_event (
            event_id     uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            dimension    text NOT NULL CHECK (dimension IN ('DEVICE', 'CONTACT', 'NETWORK')),
            key_hash     text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
            action       text NOT NULL CHECK (action IN ('SIGNUP', 'VERIFICATION', 'CLAIM')),
            occurred_at  timestamptz NOT NULL
        );
        CREATE INDEX screening_velocity_lookup
            ON verify.screening_velocity_event (tenant_id, dimension, key_hash, action, occurred_at);

        CREATE TABLE verify.screening_signal (
            signal_id    uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            source       text NOT NULL CHECK (source IN ('VELOCITY', 'ADVERSE_MEDIA')),
            subject_ref  text NOT NULL CHECK (length(subject_ref) BETWEEN 1 AND 128),
            rule         text NOT NULL,
            detail       jsonb NOT NULL,
            risk_tier    text NOT NULL CHECK (risk_tier IN ('LOW', 'ELEVATED', 'HIGH')),
            raised_at    timestamptz NOT NULL
        );
        CREATE TRIGGER screening_signal_append_only BEFORE UPDATE OR DELETE ON verify.screening_signal
            FOR EACH ROW EXECUTE FUNCTION verify.screening_append_only();

        CREATE TABLE verify.screening_signal_review (
            review_id          uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            signal_id          uuid NOT NULL REFERENCES verify.screening_signal (signal_id),
            dedupe_key         text NOT NULL CHECK (length(dedupe_key) BETWEEN 1 AND 256),
            status             text NOT NULL CHECK (status IN ('OPEN', 'CHALLENGED', 'RESOLVED')),
            notify_state       text NOT NULL DEFAULT 'PENDING' CHECK (notify_state IN ('PENDING', 'SENT', 'FAILED')),
            challenge_hash     text CHECK (challenge_hash ~ '^[0-9a-f]{64}$'),
            challenge_ref      text,
            outcome            text CHECK (outcome IN ('CLEARED_BY_REVIEWER', 'CONFIRMED_RISK', 'ESCALATED')),
            resolved_by        uuid,
            rationale          text,
            evidence_ref       text,
            created_at         timestamptz NOT NULL,
            resolved_at        timestamptz,
            CHECK ((status = 'RESOLVED') = (outcome IS NOT NULL AND evidence_ref IS NOT NULL AND resolved_by IS NOT NULL))
        );
        CREATE UNIQUE INDEX screening_signal_review_one_open
            ON verify.screening_signal_review (tenant_id, dedupe_key) WHERE status <> 'RESOLVED';
    """)
    for table in TABLES:
        _run(_RLS.format(t=table))


def downgrade() -> None:
    pass
