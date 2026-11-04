"""Sign-up records follow an erased account (ZID-UX-WF-013A, PRIV-001; slice L-11, CR-048).

Revision ID: onboarding_0007
Revises: onboarding_0006
Create Date: 2026-11-03

The account index (keyed address hash to account) and the sign-up requests were kept for as long as the account lived and could never be
deleted, so an erased person still left a pseudonymous link behind and their address could never register again. The governed erasure of the
account now removes them, and only it: a delete of the account index, or of a request younger than its 30 days, succeeds only inside a
transaction that the privacy erasure marked (`app.registration_erasure`). Every other delete, and every update, is still refused.
The account index gets its own guard so the other append-only registration tables are not loosened.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0007"
down_revision = "onboarding_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE FUNCTION platform.registration_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND coalesce(current_setting('app.registration_erasure', true), '') = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'the account index is append-only; only the erasure of the account removes a row'
                USING ERRCODE = 'check_violation';
        END $$;
        DROP TRIGGER registration_account_append_only ON platform.registration_account;
        CREATE TRIGGER registration_account_guard BEFORE UPDATE OR DELETE ON platform.registration_account
            FOR EACH ROW EXECUTE FUNCTION platform.registration_account_guard();

        CREATE OR REPLACE FUNCTION platform.registration_intent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF coalesce(current_setting('app.registration_erasure', true), '') = 'on' THEN
                    RETURN OLD;
                END IF;
                IF OLD.state IN ('INITIATED', 'VERIFICATION_SENT') OR OLD.updated_at > now() - interval '30 days' THEN
                    RAISE EXCEPTION 'a registration request is kept until it has ended and 30 days have passed'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF (NEW.intent_id, NEW.tenant_id, NEW.request_ref, NEW.country, NEW.journey, NEW.email_hash, NEW.home_cell_id,
                NEW.notice_version, NEW.created_at)
               IS DISTINCT FROM
               (OLD.intent_id, OLD.tenant_id, OLD.request_ref, OLD.country, OLD.journey, OLD.email_hash, OLD.home_cell_id,
                OLD.notice_version, OLD.created_at) THEN
                RAISE EXCEPTION 'a registration request cannot be rewritten' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('COMPLETED', 'EXPIRED', 'RISK_HOLD') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'registration request % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.email_pending IS NOT NULL AND NEW.email_pending IS DISTINCT FROM OLD.email_pending THEN
                RAISE EXCEPTION 'the address is written once, then only cleared' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.account_tenant_id IS NOT NULL AND NEW.account_tenant_id IS DISTINCT FROM OLD.account_tenant_id THEN
                RAISE EXCEPTION 'an account is bound once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    pass  # the stricter guards are restored by re-running 0003 and 0004; keeping these is harmless until the erasure adapter is removed
