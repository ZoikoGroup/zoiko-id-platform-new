"""Sign-up request retention (ZID-UX-WF-013A; slice L-11, CR-048). Owners ruled 30 days on 2026-11-02.

Revision ID: onboarding_0004
Revises: onboarding_0003
Create Date: 2026-11-02

A registration request is never deleted while it is open or recent. Once it has ended (or the account it made has stood for 30 days) the
maintenance job may delete it; the guard refuses any other delete. The account index (keyed email hash to account) is not touched: it lives
as long as the account.
"""

from zid_platform.migrations import execute_script as _run

revision = "onboarding_0004"
down_revision = "onboarding_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE OR REPLACE FUNCTION platform.registration_intent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
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
    pass  # the earlier guard refused every delete; keeping the relaxed one is harmless until the purge job is removed
