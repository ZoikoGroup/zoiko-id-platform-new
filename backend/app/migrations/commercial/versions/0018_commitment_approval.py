"""Customer commitment approval: delegated spend limits, requests and approvals (ZID-COM-CL-004 section 04; slice L-14c, CR-054).

Revision ID: commercial_0018
Revises: commercial_0017
Create Date: 2026-11-03

commitment_delegate: what the customer Owner gave another person: a spend limit for a Billing Admin, or a Procurement Approver role with a limit, each
dated and revocable (revoked_at; the row stays). A revoked or lapsed delegate has no authority on the next call.
commitment_request: one new or increased contractual obligation (a signed order), its exposure worked out by the server from the order and never from the
caller, the facts frozen in a snapshot and a hash. Facts never change after insert; only the state moves; a Zoiko review mark is set once.
commitment_approval: one signature per person per request, bound to the snapshot hash, with when the strong authentication happened; append-only.
All three are tenant-scoped: a customer sees only its own.
"""

from zid_platform.migrations import execute_script as _run

revision = "commercial_0018"
down_revision = "commercial_0017"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE commercial.commitment_delegate (
            delegate_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            principal_id   uuid NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('BILLING_SPEND', 'PROCUREMENT_APPROVER')),
            cap_usd_minor  bigint NOT NULL CHECK (cap_usd_minor > 0),
            effective_from timestamptz NOT NULL,
            effective_to   timestamptz,
            granted_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            revoked_at     timestamptz,
            revoked_by     uuid,
            CHECK (effective_to IS NULL OR effective_to > effective_from),
            CHECK (principal_id <> granted_by)
        );
        CREATE UNIQUE INDEX commitment_delegate_live ON commercial.commitment_delegate (tenant_id, principal_id, kind)
            WHERE revoked_at IS NULL;
        CREATE FUNCTION commercial.commitment_delegate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a delegation is never deleted; it is revoked' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.delegate_id, NEW.tenant_id, NEW.principal_id, NEW.kind, NEW.cap_usd_minor, NEW.effective_from, NEW.effective_to, NEW.granted_by)
               IS DISTINCT FROM
               (OLD.delegate_id, OLD.tenant_id, OLD.principal_id, OLD.kind, OLD.cap_usd_minor, OLD.effective_from, OLD.effective_to, OLD.granted_by)
               OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
                RAISE EXCEPTION 'a delegation never changes; revoke it and give a new one' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER commitment_delegate_guard BEFORE UPDATE OR DELETE ON commercial.commitment_delegate
            FOR EACH ROW EXECUTE FUNCTION commercial.commitment_delegate_guard();

        CREATE TABLE commercial.commitment_request (
            request_id         uuid PRIMARY KEY,
            tenant_id          uuid NOT NULL,
            order_id           uuid NOT NULL,
            quote_digest       text NOT NULL,
            account_id         uuid NOT NULL,
            currency           text NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
            monthly_minor      bigint NOT NULL CHECK (monthly_minor >= 0),
            term_months        integer NOT NULL CHECK (term_months >= 1),
            exposure_minor     bigint NOT NULL CHECK (exposure_minor >= 0),
            exposure_usd_minor bigint NOT NULL CHECK (exposure_usd_minor >= 0),
            tier               text NOT NULL CHECK (tier IN ('T1', 'T2', 'T3')),
            review_required    boolean NOT NULL,
            snapshot           jsonb NOT NULL,
            snapshot_hash      text NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{{64}}$'),
            policy_version     text NOT NULL,
            policy_hash        text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{{64}}$'),
            state              text NOT NULL CHECK (state IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELED')),
            requested_by       uuid NOT NULL,
            created_at         timestamptz NOT NULL,
            expires_at         timestamptz NOT NULL,
            approved_at        timestamptz,
            approval_expires_at timestamptz,
            reviewed_by        uuid,
            reviewed_at        timestamptz
        );
        CREATE UNIQUE INDEX commitment_request_live_order ON commercial.commitment_request (order_id)
            WHERE state IN ('PENDING_APPROVAL', 'APPROVED');
        CREATE INDEX commitment_request_tenant ON commercial.commitment_request (tenant_id, created_at);
        CREATE FUNCTION commercial.commitment_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'commitment requests are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.request_id, NEW.tenant_id, NEW.order_id, NEW.quote_digest, NEW.account_id, NEW.currency, NEW.monthly_minor, NEW.term_months,
                NEW.exposure_minor, NEW.exposure_usd_minor, NEW.tier, NEW.review_required, NEW.snapshot, NEW.snapshot_hash, NEW.policy_version,
                NEW.policy_hash, NEW.requested_by, NEW.created_at, NEW.expires_at)
               IS DISTINCT FROM
               (OLD.request_id, OLD.tenant_id, OLD.order_id, OLD.quote_digest, OLD.account_id, OLD.currency, OLD.monthly_minor, OLD.term_months,
                OLD.exposure_minor, OLD.exposure_usd_minor, OLD.tier, OLD.review_required, OLD.snapshot, OLD.snapshot_hash, OLD.policy_version,
                OLD.policy_hash, OLD.requested_by, OLD.created_at, OLD.expires_at) THEN
                RAISE EXCEPTION 'a commitment request is immutable; a material change is a new request' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.state IN ('REJECTED', 'EXPIRED', 'CANCELED') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'commitment request % is final', OLD.state USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.reviewed_by IS NOT NULL AND (NEW.reviewed_by, NEW.reviewed_at) IS DISTINCT FROM (OLD.reviewed_by, OLD.reviewed_at) THEN
                RAISE EXCEPTION 'a review mark is set once' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER commitment_request_guard BEFORE UPDATE OR DELETE ON commercial.commitment_request
            FOR EACH ROW EXECUTE FUNCTION commercial.commitment_request_guard();

        CREATE TABLE commercial.commitment_approval (
            request_id    uuid NOT NULL REFERENCES commercial.commitment_request (request_id),
            tenant_id     uuid NOT NULL,
            principal_id  uuid NOT NULL,
            authority     text NOT NULL CHECK (authority IN ('OWNER', 'BILLING_SPEND', 'PROCUREMENT_APPROVER')),
            decision      text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
            snapshot_hash text NOT NULL,
            reason        text CHECK (reason IS NULL OR length(reason) <= 500),
            step_up_at    timestamptz,
            decided_at    timestamptz NOT NULL,
            PRIMARY KEY (request_id, principal_id)
        );
        CREATE TRIGGER commitment_approval_append_only BEFORE UPDATE OR DELETE ON commercial.commitment_approval
            FOR EACH ROW EXECUTE FUNCTION commercial.finance_append_only();

        ALTER TABLE commercial.commitment_delegate ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.commitment_delegate FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.commitment_delegate USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE commercial.commitment_request ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.commitment_request FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.commitment_request USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE commercial.commitment_approval ENABLE ROW LEVEL SECURITY;
        ALTER TABLE commercial.commitment_approval FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON commercial.commitment_approval USING ({TENANT}) WITH CHECK ({TENANT});
    """)


def downgrade() -> None:
    pass
