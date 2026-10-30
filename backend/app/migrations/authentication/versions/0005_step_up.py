"""Step-up challenges and proofs (ZID-ENG-DEC-009 section 05; slice L-9, CR-046).

Revision ID: authentication_0005
Revises: authentication_0004
Create Date: 2026-10-30

step_up_challenge: one command draft that someone must prove they mean. It names the actor, tenant, session, action, resource and the
SHA-256 of the exact command (`command_hash`), and the passkey is challenged with bytes derived from that digest, so the signature
covers the command. A challenge only moves PENDING to VERIFIED, CANCELLED or EXPIRED, once; nothing else about it changes.

step_up_proof: what a verified challenge leaves behind: which authenticator, how strong, when (`verified_at`, the server's clock), until
when, and whether it may be used once. The row never changes except to record its consumption: `consumed_at` is set at most once,
inside the transaction that executes the command, and a second use finds it already set. Proofs hold no secrets and no biometric
material: ids, a method name, assurance labels and times.
"""

from zid_platform.migrations import execute_script as _run

revision = "authentication_0005"
down_revision = "authentication_0004"
branch_labels = None
depends_on = None

TENANT = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _run(f"""
        -- an action-bound challenge has its own ceremony, so login and session step-up can never claim (and burn) it
        ALTER TABLE authn.webauthn_challenge DROP CONSTRAINT webauthn_challenge_ceremony_check;
        ALTER TABLE authn.webauthn_challenge ADD CONSTRAINT webauthn_challenge_ceremony_check
            CHECK (ceremony IN ('REGISTRATION', 'AUTHENTICATION', 'STEP_UP', 'ACTION_STEP_UP'));

        CREATE TABLE authn.step_up_challenge (
            challenge_id        uuid PRIMARY KEY,
            tenant_id           uuid NOT NULL,
            actor_id            uuid NOT NULL,
            session_id          uuid,
            action_id           text NOT NULL CHECK (action_id ~ '^[a-z0-9-]{{3,120}}$'),
            family              text NOT NULL CHECK (char_length(family) BETWEEN 3 AND 60),
            tier                text NOT NULL CHECK (tier IN ('S1', 'S2')),
            resource_id         text NOT NULL CHECK (char_length(resource_id) BETWEEN 1 AND 256),
            command_hash        text NOT NULL CHECK (command_hash ~ '^[0-9a-f]{{64}}$'),
            summary             text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 500),
            requested_assurance text NOT NULL CHECK (char_length(requested_assurance) BETWEEN 1 AND 60),
            nonce               bytea NOT NULL CHECK (octet_length(nonce) = 32),
            policy_version      text NOT NULL,
            status              text NOT NULL DEFAULT 'PENDING'
                                    CHECK (status IN ('PENDING', 'VERIFIED', 'CANCELLED', 'EXPIRED')),
            created_at          timestamptz NOT NULL,
            expires_at          timestamptz NOT NULL,
            resolved_at         timestamptz,
            CHECK (expires_at > created_at),
            CHECK ((status = 'PENDING') = (resolved_at IS NULL))
        );
        CREATE INDEX step_up_challenge_actor ON authn.step_up_challenge (tenant_id, actor_id, created_at DESC);

        CREATE TABLE authn.step_up_proof (
            proof_id         uuid PRIMARY KEY,
            challenge_id     uuid NOT NULL UNIQUE REFERENCES authn.step_up_challenge (challenge_id),
            tenant_id        uuid NOT NULL,
            actor_id         uuid NOT NULL,
            session_id       uuid,
            authenticator_id uuid NOT NULL,
            method           text NOT NULL CHECK (method IN ('PASSKEY', 'ENTERPRISE_MFA')),
            assurance        jsonb NOT NULL,
            auth_time        timestamptz NOT NULL,
            verified_at      timestamptz NOT NULL,
            binding_hash     text NOT NULL CHECK (binding_hash ~ '^[0-9a-f]{{64}}$'),
            expires_at       timestamptz NOT NULL,
            single_use       boolean NOT NULL,
            consumed_at      timestamptz,
            consumed_for     text CHECK (consumed_for IS NULL OR char_length(consumed_for) <= 160),
            CHECK (expires_at > verified_at),
            CHECK ((consumed_at IS NULL) = (consumed_for IS NULL))
        );

        ALTER TABLE authn.step_up_challenge ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authn.step_up_challenge FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authn.step_up_challenge
            USING ({TENANT}) WITH CHECK ({TENANT});
        ALTER TABLE authn.step_up_proof ENABLE ROW LEVEL SECURITY;
        ALTER TABLE authn.step_up_proof FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON authn.step_up_proof
            USING ({TENANT}) WITH CHECK ({TENANT});

        CREATE FUNCTION authn.step_up_challenge_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'step-up challenges are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.status <> 'PENDING' OR NEW.status = 'PENDING'
               OR (NEW.challenge_id, NEW.tenant_id, NEW.actor_id, NEW.session_id, NEW.action_id, NEW.family, NEW.tier,
                   NEW.resource_id, NEW.command_hash, NEW.summary, NEW.requested_assurance, NEW.nonce,
                   NEW.policy_version, NEW.created_at, NEW.expires_at)
                  IS DISTINCT FROM
                  (OLD.challenge_id, OLD.tenant_id, OLD.actor_id, OLD.session_id, OLD.action_id, OLD.family, OLD.tier,
                   OLD.resource_id, OLD.command_hash, OLD.summary, OLD.requested_assurance, OLD.nonce,
                   OLD.policy_version, OLD.created_at, OLD.expires_at) THEN
                RAISE EXCEPTION 'a step-up challenge only resolves once; its terms do not change'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER step_up_challenge_guard
            BEFORE UPDATE OR DELETE ON authn.step_up_challenge
            FOR EACH ROW EXECUTE FUNCTION authn.step_up_challenge_guard();

        CREATE FUNCTION authn.step_up_proof_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'step-up proofs are never deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.consumed_at IS NOT NULL
               OR (NEW.proof_id, NEW.challenge_id, NEW.tenant_id, NEW.actor_id, NEW.session_id, NEW.authenticator_id,
                   NEW.method, NEW.assurance, NEW.auth_time, NEW.verified_at, NEW.binding_hash, NEW.expires_at,
                   NEW.single_use)
                  IS DISTINCT FROM
                  (OLD.proof_id, OLD.challenge_id, OLD.tenant_id, OLD.actor_id, OLD.session_id, OLD.authenticator_id,
                   OLD.method, OLD.assurance, OLD.auth_time, OLD.verified_at, OLD.binding_hash, OLD.expires_at,
                   OLD.single_use) THEN
                RAISE EXCEPTION 'a step-up proof is consumed once and otherwise never changes'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER step_up_proof_guard
            BEFORE UPDATE OR DELETE ON authn.step_up_proof
            FOR EACH ROW EXECUTE FUNCTION authn.step_up_proof_guard();
    """)


def downgrade() -> None:
    _run("""
        DELETE FROM authn.webauthn_challenge WHERE ceremony = 'ACTION_STEP_UP';
        ALTER TABLE authn.webauthn_challenge DROP CONSTRAINT webauthn_challenge_ceremony_check;
        ALTER TABLE authn.webauthn_challenge ADD CONSTRAINT webauthn_challenge_ceremony_check
            CHECK (ceremony IN ('REGISTRATION', 'AUTHENTICATION', 'STEP_UP'));
        DROP TABLE authn.step_up_proof;
        DROP TABLE authn.step_up_challenge;
        DROP FUNCTION authn.step_up_proof_guard();
        DROP FUNCTION authn.step_up_challenge_guard();
    """)
