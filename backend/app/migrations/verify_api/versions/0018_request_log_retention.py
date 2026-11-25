"""Retention for the partner request log (ZID-CP-WF10 v2.0, WF-10.10; CR-173).

Revision ID: verify_api_0018
Revises: verify_api_0017
Create Date: 2026-11-25

The request log is append-only, and a log kept for ever is a privacy cost with no benefit. So the log has ONE way to lose rows: `verify.api_purge_request_log(days)`, which removes rows older
than the retention period and never any newer row (the period cannot be set below 90 days). Everything else, including a direct DELETE or an UPDATE, is still refused. The period is a
default the owners should confirm; 90 days is the shortest the function will accept.
"""

from zid_platform.migrations import execute_script as _run

revision = "verify_api_0018"
down_revision = "verify_api_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        DROP TRIGGER api_request_log_append_only ON verify.api_request_log;
        CREATE FUNCTION verify.api_request_log_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('app.verify_api_retention', true) = 'on' AND OLD.occurred_at < now() - interval '90 days' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'verify.api_request_log is append-only (% refused)', TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER api_request_log_append_only BEFORE UPDATE OR DELETE ON verify.api_request_log FOR EACH ROW EXECUTE FUNCTION verify.api_request_log_guard();
        CREATE POLICY retention_purge ON verify.api_request_log FOR DELETE USING (current_setting('app.verify_api_retention', true) = 'on' AND occurred_at < now() - interval '90 days');

        CREATE FUNCTION verify.api_purge_request_log(retention_days integer DEFAULT 90) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = verify, pg_temp AS $$
        DECLARE removed bigint;
        BEGIN
            IF retention_days < 90 THEN
                RAISE EXCEPTION 'the request log is kept for at least 90 days' USING ERRCODE = 'check_violation';
            END IF;
            PERFORM set_config('app.verify_api_retention', 'on', true);
            DELETE FROM verify.api_request_log WHERE occurred_at < now() - make_interval(days => retention_days);
            GET DIAGNOSTICS removed = ROW_COUNT;
            PERFORM set_config('app.verify_api_retention', '', true);
            RETURN removed;
        END $$;
    """)


def downgrade() -> None:
    _run("""
        DROP FUNCTION verify.api_purge_request_log(integer);
        DROP POLICY retention_purge ON verify.api_request_log;
        DROP TRIGGER api_request_log_append_only ON verify.api_request_log;
        DROP FUNCTION verify.api_request_log_guard();
        CREATE TRIGGER api_request_log_append_only BEFORE UPDATE OR DELETE ON verify.api_request_log FOR EACH ROW EXECUTE FUNCTION verify.api_dev_append_only();
    """)
