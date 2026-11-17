"""Company-verification market policy packs: immutable versions, bound approvals, effective intervals (ZID-DTV-WF-02 K09; CR-109).

Revision ID: organization_0012
Revises: organization_0011
Create Date: 2026-11-17

kyb_market_policy: one row per version of a pack, identified by the digest of its content; never changed or deleted. kyb_market_policy_approval: a sign-off by a person in one of the four roles,
bound to the exact digest (an approval of another version is another row); the person who authored the version cannot approve it (the table repeats the rule). kyb_market_policy_activation: when a version
was in effect for its market; the only change a row takes is its end, once, and at most one interval per market is open. A decision records the digest of the pack that made it, so it replays against
that pack whatever has been published since. These are platform configuration, not company data: no row-level security by company.
"""

from zid_platform.migrations import execute_script as _run

revision = "organization_0012"
down_revision = "organization_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _run("""
        CREATE TABLE org.kyb_market_policy (
            policy_hash  text PRIMARY KEY CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            market_key   text NOT NULL CHECK (length(market_key) BETWEEN 3 AND 200),
            version      text NOT NULL CHECK (length(version) BETWEEN 1 AND 80),
            body         jsonb NOT NULL,
            author       uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (market_key, version)
        );

        CREATE TABLE org.kyb_market_policy_approval (
            approval_id  uuid PRIMARY KEY,
            policy_hash  text NOT NULL REFERENCES org.kyb_market_policy (policy_hash),
            person       uuid NOT NULL,
            role         text NOT NULL CHECK (role IN ('COMPLIANCE', 'LEGAL', 'PRIVACY', 'SECURITY')),
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (policy_hash, person, role)
        );

        CREATE TABLE org.kyb_market_policy_activation (
            activation_id uuid PRIMARY KEY,
            market_key    text NOT NULL,
            policy_hash   text NOT NULL REFERENCES org.kyb_market_policy (policy_hash),
            kind          text NOT NULL CHECK (kind IN ('PUBLISH', 'ROLLBACK')),
            activated_by  uuid NOT NULL,
            note          text CHECK (note IS NULL OR length(note) <= 2000),
            activated_at  timestamptz NOT NULL,
            ended_at      timestamptz CHECK (ended_at IS NULL OR ended_at >= activated_at)
        );
        CREATE UNIQUE INDEX kyb_market_policy_one_open ON org.kyb_market_policy_activation (market_key) WHERE ended_at IS NULL;

        CREATE FUNCTION org.kyb_market_policy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a published policy version never changes and is never deleted' USING ERRCODE = 'check_violation';
        END $$;
        CREATE TRIGGER kyb_market_policy_guard BEFORE UPDATE OR DELETE ON org.kyb_market_policy
            FOR EACH ROW EXECUTE FUNCTION org.kyb_market_policy_guard();

        CREATE FUNCTION org.kyb_market_policy_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'a policy approval never changes and is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.person = (SELECT author FROM org.kyb_market_policy WHERE policy_hash = NEW.policy_hash) THEN
                RAISE EXCEPTION 'the author of a policy version cannot approve it' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER kyb_market_policy_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON org.kyb_market_policy_approval
            FOR EACH ROW EXECUTE FUNCTION org.kyb_market_policy_approval_guard();

        CREATE FUNCTION org.kyb_market_policy_activation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'an activation is never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.ended_at IS NOT NULL OR NEW.market_key <> OLD.market_key OR NEW.policy_hash <> OLD.policy_hash OR NEW.kind <> OLD.kind
               OR NEW.activated_by <> OLD.activated_by OR NEW.activated_at <> OLD.activated_at OR NEW.activation_id <> OLD.activation_id
               OR NEW.note IS DISTINCT FROM OLD.note THEN
                RAISE EXCEPTION 'an activation changes only by ending, once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER kyb_market_policy_activation_guard BEFORE UPDATE OR DELETE ON org.kyb_market_policy_activation
            FOR EACH ROW EXECUTE FUNCTION org.kyb_market_policy_activation_guard();
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE org.kyb_market_policy_activation;
        DROP TABLE org.kyb_market_policy_approval;
        DROP TABLE org.kyb_market_policy;
        DROP FUNCTION org.kyb_market_policy_activation_guard();
        DROP FUNCTION org.kyb_market_policy_approval_guard();
        DROP FUNCTION org.kyb_market_policy_guard();
    """)
