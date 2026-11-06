"""The table that holds software keys (CR-060), written once and created in two places: `keys.key_version` in a cell's database
and `global_directory.key_version` in the global plane's. Both are platform tables, not tenant data, so there is no row-level
security; the runtime and worker roles get SELECT, INSERT and UPDATE only, and a trigger makes the rows append-only apart from
the lifecycle (ACTIVE -> RETIRED -> DESTROYED), a re-wrap under a newer master key, and destruction, which erases the material.
"""

from __future__ import annotations


def key_version_ddl(schema: str) -> str:
    return f"""
        CREATE TABLE {schema}.key_version (
            key_id       text        NOT NULL CHECK (char_length(key_id) BETWEEN 1 AND 200),
            version      integer     NOT NULL CHECK (version >= 1),
            purpose      text        NOT NULL,
            algorithm    text        NOT NULL CHECK (algorithm IN ('AES-256-GCM', 'HMAC-SHA-256', 'ES256')),
            state        text        NOT NULL CHECK (state IN ('ACTIVE', 'RETIRED', 'PENDING_DESTRUCTION', 'DESTROYED')),
            wrapped      bytea,
            public_jwk   jsonb,
            pq_ready     boolean     NOT NULL DEFAULT false,
            created_at   timestamptz NOT NULL,
            activated_at timestamptz,
            retired_at   timestamptz,
            destroyed_at timestamptz,
            PRIMARY KEY (key_id, version),
            CHECK ((state = 'DESTROYED') = (wrapped IS NULL))
        );

        CREATE FUNCTION {schema}.key_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'key versions are never deleted: destroy the material instead';
            END IF;
            IF NEW.key_id <> OLD.key_id OR NEW.version <> OLD.version OR NEW.purpose <> OLD.purpose
               OR NEW.algorithm <> OLD.algorithm OR NEW.created_at <> OLD.created_at
               OR NEW.public_jwk IS DISTINCT FROM OLD.public_jwk THEN
                RAISE EXCEPTION 'the identity of a key version never changes';
            END IF;
            IF OLD.state = 'DESTROYED' AND (NEW.state <> 'DESTROYED' OR NEW.wrapped IS NOT NULL) THEN
                RAISE EXCEPTION 'a destroyed key stays destroyed';
            END IF;
            IF OLD.state = 'RETIRED' AND NEW.state = 'ACTIVE' THEN
                RAISE EXCEPTION 'a retired key is not reactivated';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER key_version_guard BEFORE UPDATE OR DELETE ON {schema}.key_version
            FOR EACH ROW EXECUTE FUNCTION {schema}.key_version_guard();
    """


def key_version_drop_ddl(schema: str) -> str:
    return f"""
        DROP TRIGGER key_version_guard ON {schema}.key_version;
        DROP FUNCTION {schema}.key_version_guard();
        DROP TABLE {schema}.key_version;
    """


def key_event_ddl(schema: str) -> str:
    """Every change to a key, written in the same transaction as the change itself. The evidence worker copies each row into the
    ledger (platform-security partition) and stamps `ledgered_at`; nothing else about a row can change, and no row can be deleted."""
    return f"""
        CREATE TABLE {schema}.key_event (
            event_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            key_id      text        NOT NULL,
            version     integer     NOT NULL,
            action      text        NOT NULL CHECK (action IN ('CREATED', 'ROTATED', 'RETIRED', 'DESTROYED', 'REWRAPPED')),
            purpose     text        NOT NULL,
            algorithm   text        NOT NULL,
            actor       text        NOT NULL CHECK (char_length(actor) BETWEEN 1 AND 200),
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            ledgered_at timestamptz
        );
        CREATE INDEX key_event_unledgered ON {schema}.key_event (occurred_at) WHERE ledgered_at IS NULL;

        CREATE FUNCTION {schema}.key_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'key events are never deleted';
            END IF;
            IF NEW.event_id <> OLD.event_id OR NEW.key_id <> OLD.key_id OR NEW.version <> OLD.version OR NEW.action <> OLD.action
               OR NEW.purpose <> OLD.purpose OR NEW.algorithm <> OLD.algorithm OR NEW.actor <> OLD.actor
               OR NEW.occurred_at <> OLD.occurred_at THEN
                RAISE EXCEPTION 'a key event never changes';
            END IF;
            IF OLD.ledgered_at IS NOT NULL OR NEW.ledgered_at IS NULL THEN
                RAISE EXCEPTION 'a key event is ledgered once';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER key_event_guard BEFORE UPDATE OR DELETE ON {schema}.key_event
            FOR EACH ROW EXECUTE FUNCTION {schema}.key_event_guard();
    """


def key_event_drop_ddl(schema: str) -> str:
    return f"""
        DROP TRIGGER key_event_guard ON {schema}.key_event;
        DROP FUNCTION {schema}.key_event_guard();
        DROP TABLE {schema}.key_event;
    """
