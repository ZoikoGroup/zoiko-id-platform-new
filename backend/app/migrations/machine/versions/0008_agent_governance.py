"""Agent governance: policy versions, change approvals, budgets, reservations, runs, the evidence stream and freezes (ZID-AG-WF-002; CR-091).

Revision ID: machine_0008
Revises: machine_0007
Create Date: 2026-11-14

Rows hold ids, hashes, codes, counts and exact decimal amounts only: never a secret, a document, a prompt or a name.
Immutable by trigger: a policy version's content and hash, a change request's frozen request, a budget revision's cap and a
reservation's identity (a cap is changed by a new revision, never edited; a reservation settles exactly once). Append-only by
trigger: signatures, budget adjustments, the evidence event chain. Every table is tenant-scoped under row-level security.
Money is `numeric`, never float.
"""

from zid_platform.migrations import execute_script as _run

revision = "machine_0008"
down_revision = "machine_0007"
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
TABLES = (
    "governance_policy", "governance_change_request", "governance_signature", "governance_delegate", "company_baseline",
    "budget", "budget_reservation", "budget_adjustment", "run_ledger", "governance_event", "freeze_control",
)  # fmt: skip
APPEND_ONLY = frozenset(
    ("governance_signature", "budget_adjustment", "governance_event")
)  # a set, so TABLES stays the one tuple constant


def upgrade() -> None:
    _run("""
        CREATE TABLE agent.governance_policy (
            policy_id            uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            agent_principal_id   uuid NOT NULL,
            version              integer NOT NULL CHECK (version >= 1),
            risk_tier            text NOT NULL CHECK (risk_tier IN ('LOW','MEDIUM','HIGH')),
            state                text NOT NULL CHECK (state IN ('DRAFT','PENDING_APPROVAL','ACTIVE','SUPERSEDED','REJECTED')),
            region               text NOT NULL CHECK (char_length(region) BETWEEN 2 AND 64),
            content              jsonb NOT NULL,
            policy_hash          text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            expires_at           timestamptz,
            approval_request_id  uuid,
            created_by           uuid NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, agent_principal_id, version)
        );
        CREATE UNIQUE INDEX governance_policy_one_active ON agent.governance_policy (tenant_id, agent_principal_id)
            WHERE state = 'ACTIVE';

        CREATE TABLE agent.governance_change_request (
            request_id           uuid PRIMARY KEY,
            ref_no               bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id            uuid NOT NULL,
            agent_principal_id   uuid NOT NULL,
            subject              text NOT NULL CHECK (subject IN ('POLICY','BUDGET','RESTORE')),
            kind                 text NOT NULL CHECK (kind IN ('PUBLISH','SCOPE_EXTENSION','AI_ENABLE','BUDGET_INCREASE',
                                                               'QUOTA_INCREASE','RESTORE','PRIVILEGE_MUTATION')),
            requested_by         uuid NOT NULL,
            authors              uuid[] NOT NULL,
            policy_id            uuid REFERENCES agent.governance_policy (policy_id),
            policy_hash          text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            base_policy_hash     text,
            delta                jsonb NOT NULL,
            delta_hash           text NOT NULL CHECK (delta_hash ~ '^sha256:[0-9a-f]{64}$'),
            required             text[] NOT NULL,
            payload              jsonb NOT NULL DEFAULT '{}'::jsonb,
            reason_code          text NOT NULL CHECK (char_length(reason_code) BETWEEN 2 AND 64),
            evidence_ref         text,
            state                text NOT NULL CHECK (state IN ('PENDING','CLARIFICATION','APPROVED','REJECTED','EXPIRED',
                                                                'INVALIDATED','APPLIED')),
            decided_by           uuid,
            decided_reason       text,
            created_at           timestamptz NOT NULL DEFAULT now(),
            expires_at           timestamptz NOT NULL,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX governance_change_request_agent ON agent.governance_change_request (tenant_id, agent_principal_id, created_at DESC);

        CREATE TABLE agent.governance_signature (
            request_id           uuid NOT NULL REFERENCES agent.governance_change_request (request_id),
            tenant_id            uuid NOT NULL,
            slot                 text NOT NULL CHECK (slot IN ('SECURITY','AUTHORITY','PRIVACY','FINANCE')),
            signer               uuid NOT NULL,
            policy_hash          text NOT NULL CHECK (policy_hash ~ '^sha256:[0-9a-f]{64}$'),
            signed_at            timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (request_id, slot),
            UNIQUE (request_id, signer)
        );

        CREATE TABLE agent.governance_delegate (
            delegate_id          uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            principal_id         uuid NOT NULL,
            slot                 text NOT NULL CHECK (slot IN ('SECURITY','AUTHORITY','PRIVACY','FINANCE')),
            granted_by           uuid NOT NULL,
            granted_at           timestamptz NOT NULL DEFAULT now(),
            revoked_by           uuid,
            revoked_at           timestamptz,
            CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
        );
        CREATE UNIQUE INDEX governance_delegate_active ON agent.governance_delegate (tenant_id, principal_id, slot)
            WHERE revoked_at IS NULL;

        CREATE TABLE agent.company_baseline (
            tenant_id            uuid PRIMARY KEY,
            allowed_tools        text[] NOT NULL DEFAULT '{}',
            allowed_regions      text[] NOT NULL DEFAULT '{}',
            ai_providers         text[] NOT NULL DEFAULT '{}',
            quotas               jsonb NOT NULL,
            schedules_allowed    boolean NOT NULL DEFAULT false,
            frozen               boolean NOT NULL DEFAULT false,
            revision             integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            updated_by           uuid NOT NULL,
            updated_at           timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE agent.budget (
            budget_id            uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            scope                text NOT NULL CHECK (scope IN ('COMPANY','WORKSPACE','AGENT','CALL')),
            agent_principal_id   uuid,
            currency             text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            period               text NOT NULL CHECK (period IN ('DAY','MONTH','CALL')),
            hard_cap             numeric(24,6) NOT NULL CHECK (hard_cap >= 0),
            warn_pct_1           integer NOT NULL DEFAULT 70 CHECK (warn_pct_1 BETWEEN 1 AND 99),
            warn_pct_2           integer NOT NULL DEFAULT 90 CHECK (warn_pct_2 BETWEEN 1 AND 99),
            parent_id            uuid REFERENCES agent.budget (budget_id),
            revision             integer NOT NULL CHECK (revision >= 1),
            state                text NOT NULL CHECK (state IN ('PENDING','ACTIVE','SUPERSEDED')),
            approval_request_id  uuid REFERENCES agent.governance_change_request (request_id),
            effective_from       timestamptz,
            created_by           uuid NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            CHECK ((scope = 'AGENT') = (agent_principal_id IS NOT NULL)),
            CHECK (warn_pct_1 < warn_pct_2),
            CHECK ((period = 'CALL') = (scope = 'CALL'))
        );
        CREATE UNIQUE INDEX budget_one_active ON agent.budget
            (tenant_id, scope, coalesce(agent_principal_id, '00000000-0000-0000-0000-000000000000'::uuid), period, currency)
            WHERE state = 'ACTIVE';

        CREATE TABLE agent.budget_reservation (
            reservation_id       uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            agent_principal_id   uuid NOT NULL,
            run_id               uuid,
            tool_id              text NOT NULL,
            spend_class          text NOT NULL CHECK (spend_class IN ('AI_INFERENCE','INTERNAL')),
            currency             text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            upper_bound          numeric(24,6) NOT NULL CHECK (upper_bound >= 0),
            committed            numeric(24,6) NOT NULL DEFAULT 0 CHECK (committed >= 0),
            released             numeric(24,6) NOT NULL DEFAULT 0 CHECK (released >= 0),
            overrun              numeric(24,6) NOT NULL DEFAULT 0 CHECK (overrun >= 0),
            state                text NOT NULL CHECK (state IN ('RESERVED','SETTLED','RELEASED')),
            idempotency_key      text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
            price_version        text NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            expires_at           timestamptz NOT NULL,
            settled_at           timestamptz,
            UNIQUE (tenant_id, idempotency_key),
            CHECK (committed + released <= upper_bound OR state = 'RESERVED')
        );
        CREATE INDEX budget_reservation_window ON agent.budget_reservation (tenant_id, agent_principal_id, created_at);

        CREATE TABLE agent.budget_adjustment (
            adjustment_id        uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            reservation_id       uuid NOT NULL REFERENCES agent.budget_reservation (reservation_id),
            agent_principal_id   uuid NOT NULL,
            currency             text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            amount               numeric(24,6) NOT NULL,
            reason               text NOT NULL CHECK (reason IN ('LATE_INVOICE','OVERRUN','REFUND','REVERSAL','PRICE_REVISION')),
            created_by           uuid NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE agent.run_ledger (
            run_id               uuid PRIMARY KEY,
            tenant_id            uuid NOT NULL,
            agent_principal_id   uuid NOT NULL,
            state                text NOT NULL CHECK (state IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELED','TIMED_OUT')),
            mode                 text NOT NULL DEFAULT 'MANUAL' CHECK (mode IN ('MANUAL','SCHEDULED')),
            queued_at            timestamptz,
            idempotency_key      text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
            policy_hash          text NOT NULL,
            started_at           timestamptz NOT NULL DEFAULT now(),
            deadline_at          timestamptz NOT NULL,
            ended_at             timestamptz,
            records              integer NOT NULL DEFAULT 0 CHECK (records >= 0),
            proposals            integer NOT NULL DEFAULT 0 CHECK (proposals >= 0),
            reason_code          text,
            UNIQUE (tenant_id, idempotency_key),
            CHECK ((state IN ('QUEUED','RUNNING')) = (ended_at IS NULL))
        );
        CREATE INDEX run_ledger_agent_day ON agent.run_ledger (tenant_id, agent_principal_id, started_at DESC);

        CREATE TABLE agent.governance_event (
            seq                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_id             uuid NOT NULL UNIQUE,
            tenant_id            uuid NOT NULL,
            agent_principal_id   uuid,
            run_id               uuid,
            actor                uuid,
            actor_type           text NOT NULL CHECK (actor_type IN ('AGENT','HUMAN','SYSTEM')),
            action               text NOT NULL,
            outcome              text NOT NULL CHECK (outcome IN ('ALLOW','DENY','SHADOW_DENY','INFO')),
            code                 text,
            tool_id              text,
            resource             text,
            region               text,
            policy_hash          text,
            correlation_id       text NOT NULL,
            cost_receipt         jsonb,
            occurred_at          timestamptz NOT NULL DEFAULT now(),
            prev_digest          text NOT NULL,
            digest               text NOT NULL CHECK (digest ~ '^sha256:[0-9a-f]{64}$'),
            chain_key            text GENERATED ALWAYS AS (coalesce(agent_principal_id::text, 'tenant')) STORED
        );
        CREATE INDEX governance_event_agent ON agent.governance_event (tenant_id, agent_principal_id, seq DESC);
        CREATE INDEX governance_event_chain ON agent.governance_event (tenant_id, chain_key, seq DESC);

        CREATE TABLE agent.freeze_control (
            case_id              uuid PRIMARY KEY,
            generation           bigint GENERATED ALWAYS AS IDENTITY,
            tenant_id            uuid NOT NULL,
            scope                text NOT NULL CHECK (scope IN ('AGENT','TENANT')),
            agent_principal_id   uuid,
            initiator            uuid NOT NULL,
            reason               text NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 500),
            severity             text NOT NULL CHECK (severity IN ('P0','P1','P2')),
            issued_at            timestamptz NOT NULL DEFAULT now(),
            restore_request_id   uuid REFERENCES agent.governance_change_request (request_id),
            restored_at          timestamptz,
            restored_by          uuid[] NOT NULL DEFAULT '{}',
            CHECK ((scope = 'AGENT') = (agent_principal_id IS NOT NULL)),
            CHECK ((restored_at IS NULL) = (cardinality(restored_by) = 0))
        );
        CREATE INDEX freeze_control_active ON agent.freeze_control (tenant_id) WHERE restored_at IS NULL;

        CREATE FUNCTION agent.governance_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'this record is append-only' USING ERRCODE = '23514';
        END $$;
        CREATE FUNCTION agent.governance_policy_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.content <> OLD.content OR NEW.policy_hash <> OLD.policy_hash OR NEW.version <> OLD.version
               OR NEW.region <> OLD.region OR NEW.agent_principal_id <> OLD.agent_principal_id THEN
                RAISE EXCEPTION 'a policy version is immutable: a change is a new version' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION agent.governance_request_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.policy_hash <> OLD.policy_hash OR NEW.delta_hash <> OLD.delta_hash OR NEW.required <> OLD.required
               OR NEW.requested_by <> OLD.requested_by OR NEW.kind <> OLD.kind OR NEW.expires_at <> OLD.expires_at
               OR NEW.authors <> OLD.authors OR NEW.payload <> OLD.payload THEN
                RAISE EXCEPTION 'a change request is a snapshot: only its decision may change' USING ERRCODE = '23514';
            END IF;
            IF OLD.state IN ('REJECTED','EXPIRED','INVALIDATED','APPLIED') AND NEW.state <> OLD.state THEN
                RAISE EXCEPTION 'a decided change request is final' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION agent.governance_budget_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.hard_cap <> OLD.hard_cap OR NEW.currency <> OLD.currency OR NEW.period <> OLD.period
               OR NEW.revision <> OLD.revision OR NEW.scope <> OLD.scope THEN
                RAISE EXCEPTION 'a budget revision is immutable: a change is a new revision' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION agent.governance_reservation_once() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state <> 'RESERVED' THEN
                RAISE EXCEPTION 'a reservation settles exactly once' USING ERRCODE = '23514';
            END IF;
            IF NEW.upper_bound <> OLD.upper_bound OR NEW.agent_principal_id <> OLD.agent_principal_id
               OR NEW.currency <> OLD.currency OR NEW.idempotency_key <> OLD.idempotency_key THEN
                RAISE EXCEPTION 'a reservation keeps its identity and its amount' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION agent.governance_freeze_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.scope <> OLD.scope OR NEW.initiator <> OLD.initiator OR NEW.generation <> OLD.generation
               OR NEW.agent_principal_id IS DISTINCT FROM OLD.agent_principal_id OR NEW.issued_at <> OLD.issued_at THEN
                RAISE EXCEPTION 'a freeze keeps its facts: it can only be restored' USING ERRCODE = '23514';
            END IF;
            IF OLD.restored_at IS NOT NULL THEN
                RAISE EXCEPTION 'a restored freeze is history' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER governance_policy_frozen BEFORE UPDATE ON agent.governance_policy
            FOR EACH ROW EXECUTE FUNCTION agent.governance_policy_frozen();
        CREATE TRIGGER governance_request_frozen BEFORE UPDATE ON agent.governance_change_request
            FOR EACH ROW EXECUTE FUNCTION agent.governance_request_frozen();
        CREATE TRIGGER governance_budget_frozen BEFORE UPDATE ON agent.budget
            FOR EACH ROW EXECUTE FUNCTION agent.governance_budget_frozen();
        CREATE TRIGGER governance_reservation_once BEFORE UPDATE ON agent.budget_reservation
            FOR EACH ROW EXECUTE FUNCTION agent.governance_reservation_once();
        CREATE TRIGGER governance_freeze_guard BEFORE UPDATE ON agent.freeze_control
            FOR EACH ROW EXECUTE FUNCTION agent.governance_freeze_guard();
    """)
    for t in APPEND_ONLY:
        _run(f"""
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON agent.{t}
                FOR EACH ROW EXECUTE FUNCTION agent.governance_append_only();
        """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE agent.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE agent.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON agent.{t} USING {RULE} WITH CHECK {RULE};
        """)


def downgrade() -> None:
    for t in reversed(TABLES):
        _run(f"DROP TABLE agent.{t} CASCADE;")
    _run("""
        DROP FUNCTION agent.governance_append_only();
        DROP FUNCTION agent.governance_policy_frozen();
        DROP FUNCTION agent.governance_request_frozen();
        DROP FUNCTION agent.governance_budget_frozen();
        DROP FUNCTION agent.governance_reservation_once();
        DROP FUNCTION agent.governance_freeze_guard();
    """)
