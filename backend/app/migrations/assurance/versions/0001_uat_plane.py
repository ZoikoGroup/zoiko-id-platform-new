"""The independent assurance plane: baselines, fixtures, runs, an append-only event chain, signed manifests, release decisions (ZID-UAT-005; CR-074).

Revision ID: assurance_0001
Revises:
Create Date: 2026-11-12

Rows hold ids, digests, statuses and references only: never a biometric, a document, a name or a claim value. Append-only by trigger: the event chain,
manifests, release signatures, rollbacks and role-assignment history. A fixture revision's content hash cannot change once the revision is approved,
and a run's frozen inputs cannot change after it is created. All tables are tenant-scoped under row-level security.
"""

from zid_platform.migrations import execute_script as _run

revision = "assurance_0001"
down_revision = None
branch_labels = None
depends_on = None

RULE = "(tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
TABLES = (
    "uat_baseline", "uat_case", "uat_fixture", "uat_fixture_revision", "uat_run", "uat_event", "uat_manifest", "uat_defect",
    "uat_release_candidate", "uat_release_signature", "uat_rollback", "uat_role_assignment", "uat_policy", "uat_policy_signature", "uat_registry_snapshot",
)  # fmt: skip
APPEND_ONLY = frozenset(
    (
        "uat_event",
        "uat_manifest",
        "uat_release_signature",
        "uat_rollback",
        "uat_policy",
        "uat_policy_signature",
        "uat_registry_snapshot",
    )
)  # a set, so TABLES stays the one tuple constant


def upgrade() -> None:
    _run("""
        CREATE TABLE evidence.uat_baseline (
            baseline_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            version        text NOT NULL CHECK (char_length(version) BETWEEN 1 AND 64),
            source_sha256  text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            case_count     integer NOT NULL CHECK (case_count >= 0),
            risk_confirmed_by uuid,
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, version)
        );
        CREATE TABLE evidence.uat_case (
            baseline_id    uuid NOT NULL REFERENCES evidence.uat_baseline (baseline_id),
            tenant_id      uuid NOT NULL,
            case_id        text NOT NULL CHECK (case_id ~ '^[A-Z]{2,3}-[0-9]{2}$'),
            suite          text NOT NULL,
            risk           text NOT NULL CHECK (risk IN ('PROPOSED_P0','PROPOSED_P1','P0','P1','P2')),
            dependency     text NOT NULL CHECK (dependency IN ('OWN','THIRD_PARTY','PEOPLE')),
            assertion      text NOT NULL,
            oracle_version text NOT NULL DEFAULT 'v1',
            PRIMARY KEY (baseline_id, case_id)
        );
        CREATE TABLE evidence.uat_fixture (
            fixture_id     uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            name           text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            created_by     uuid NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, name)
        );
        CREATE TABLE evidence.uat_fixture_revision (
            revision_id    uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            fixture_id     uuid NOT NULL REFERENCES evidence.uat_fixture (fixture_id),
            revision       integer NOT NULL CHECK (revision >= 1),
            content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            author         uuid NOT NULL,
            state          text NOT NULL CHECK (state IN ('DRAFT','APPROVED','REJECTED')),
            reviewer       uuid,
            rationale      text,
            reviewed_at    timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (fixture_id, revision),
            CHECK (state = 'DRAFT' OR (reviewer IS NOT NULL AND reviewer <> author AND rationale IS NOT NULL))
        );
        CREATE TABLE evidence.uat_run (
            run_id           uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            baseline_id      uuid NOT NULL REFERENCES evidence.uat_baseline (baseline_id),
            release_unit     jsonb NOT NULL,
            environment_id   text NOT NULL,
            build_sha256     text NOT NULL CHECK (build_sha256 ~ '^[0-9a-f]{64}$'),
            config_digest    text NOT NULL CHECK (config_digest ~ '^[0-9a-f]{64}$'),
            test_set_sha256  text NOT NULL CHECK (test_set_sha256 ~ '^[0-9a-f]{64}$'),
            fixture_hashes   text[] NOT NULL,
            pins             jsonb NOT NULL DEFAULT '{}',
            kind             text NOT NULL DEFAULT 'INDEPENDENT' CHECK (kind IN ('INDEPENDENT','DEVELOPER')),
            tester_principal uuid NOT NULL,
            tester_role      text NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE evidence.uat_event (
            event_id     uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            run_id       uuid NOT NULL REFERENCES evidence.uat_run (run_id),
            seq          integer NOT NULL CHECK (seq >= 1),
            case_id      text,
            kind         text NOT NULL CHECK (char_length(kind) BETWEEN 1 AND 64),
            payload      jsonb NOT NULL,
            prev_hash    text NOT NULL CHECK (prev_hash ~ '^[0-9a-f]{64}$'),
            hash         text NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
            idem_key     text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, seq),
            UNIQUE (run_id, idem_key)
        );
        CREATE TABLE evidence.uat_manifest (
            manifest_id      uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            run_id           uuid NOT NULL REFERENCES evidence.uat_run (run_id),
            case_id          text NOT NULL,
            claimed_result   text NOT NULL,
            verdict          text NOT NULL CHECK (verdict IN ('NOT_RUN','BLOCKED','PASS','FAIL','INCONCLUSIVE','EXPIRED','WAIVED')),
            manifest         jsonb NOT NULL,
            manifest_sha256  text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
            problems         text[] NOT NULL DEFAULT '{}',
            jws              text NOT NULL,
            signed_kid       text NOT NULL,
            chain_head       text NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX uat_manifest_case ON evidence.uat_manifest (tenant_id, case_id, created_at DESC);
        CREATE TABLE evidence.uat_defect (
            defect_id   uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL,
            case_id     text NOT NULL,
            severity    text NOT NULL CHECK (severity IN ('BLOCKER','CRITICAL','MAJOR','MINOR')),
            owner       text NOT NULL,
            status      text NOT NULL CHECK (status IN ('OPEN','FIXED','ACCEPTED')),
            created_by  uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE evidence.uat_release_candidate (
            release_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            baseline_id      uuid NOT NULL REFERENCES evidence.uat_baseline (baseline_id),
            release_unit     jsonb NOT NULL,
            build_sha256     text NOT NULL CHECK (build_sha256 ~ '^[0-9a-f]{64}$'),
            policy_pack      text NOT NULL,
            snapshot         jsonb NOT NULL,
            facts            jsonb NOT NULL,
            engineering_owner uuid,
            commit_authors   text[] NOT NULL DEFAULT '{}',
            status           text NOT NULL CHECK (status IN ('OPEN','NO_GO','GO','ROLLED_BACK','LOCKED')),
            locked_reason    text,
            created_by       uuid NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE evidence.uat_release_signature (
            signature_id uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            release_id   uuid NOT NULL REFERENCES evidence.uat_release_candidate (release_id),
            role         text NOT NULL CHECK (role IN ('QA_LEAD','CTO','SECURITY_GRC','PRIVACY','COUNTRY_LEGAL')),
            principal    uuid NOT NULL,
            decision     text NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
            rationale    text NOT NULL,
            jws          text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (release_id, role)
        );
        CREATE TABLE evidence.uat_rollback (
            rollback_id  uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            release_id   uuid NOT NULL REFERENCES evidence.uat_release_candidate (release_id),
            scope        jsonb NOT NULL,
            reason       text NOT NULL,
            by_principal uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE evidence.uat_role_assignment (
            assignment_id uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            role          text NOT NULL CHECK (role IN ('QA_LEAD','CTO','SECURITY_GRC','PRIVACY','COUNTRY_LEGAL','TESTER','FIXTURE_REVIEWER')),
            principal     uuid NOT NULL,
            assigned_by   uuid NOT NULL,
            active        boolean NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK (assigned_by <> principal)
        );
        CREATE UNIQUE INDEX uat_role_one_active ON evidence.uat_role_assignment (tenant_id, role, principal) WHERE active;
        CREATE TABLE evidence.uat_registry_snapshot (
            snapshot_id   uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            source        text NOT NULL CHECK (char_length(source) BETWEEN 1 AND 64),
            simulated     boolean NOT NULL,
            services      jsonb NOT NULL,
            sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            taken_at      timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE evidence.uat_policy (
            policy_id     uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            release_unit  jsonb NOT NULL,
            thresholds    jsonb NOT NULL,
            proposed_by   uuid NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE evidence.uat_policy_signature (
            signature_id  uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            policy_id     uuid NOT NULL REFERENCES evidence.uat_policy (policy_id),
            role          text NOT NULL CHECK (role IN ('QA_LEAD','CTO','SECURITY_GRC')),
            principal     uuid NOT NULL,
            decision      text NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
            rationale     text NOT NULL,
            jws           text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (policy_id, role),
            UNIQUE (policy_id, principal)
        );

        CREATE FUNCTION evidence.uat_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'this record is append-only' USING ERRCODE = '23514';
        END $$;
        CREATE FUNCTION evidence.uat_fixture_locked() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state = 'APPROVED' OR OLD.state = 'REJECTED' THEN
                RAISE EXCEPTION 'a reviewed fixture revision is final: a change is a new revision' USING ERRCODE = '23514';
            END IF;
            IF NEW.content_sha256 <> OLD.content_sha256 OR NEW.author <> OLD.author OR NEW.revision <> OLD.revision THEN
                RAISE EXCEPTION 'only the review of a revision may change' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION evidence.uat_run_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'a run is frozen when it is created' USING ERRCODE = '23514';
        END $$;
        CREATE FUNCTION evidence.uat_candidate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.snapshot <> OLD.snapshot OR NEW.release_unit <> OLD.release_unit OR NEW.build_sha256 <> OLD.build_sha256
               OR NEW.facts <> OLD.facts THEN
                RAISE EXCEPTION 'a release candidate is a snapshot: only its status may change' USING ERRCODE = '23514';
            END IF;
            IF OLD.status = 'ROLLED_BACK' AND NEW.status <> 'ROLLED_BACK' THEN
                RAISE EXCEPTION 'a rolled-back release stays rolled back' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER uat_fixture_locked BEFORE UPDATE ON evidence.uat_fixture_revision
            FOR EACH ROW EXECUTE FUNCTION evidence.uat_fixture_locked();
        CREATE TRIGGER uat_run_frozen BEFORE UPDATE ON evidence.uat_run
            FOR EACH ROW EXECUTE FUNCTION evidence.uat_run_frozen();
        CREATE TRIGGER uat_candidate_guard BEFORE UPDATE ON evidence.uat_release_candidate
            FOR EACH ROW EXECUTE FUNCTION evidence.uat_candidate_guard();
    """)
    for t in APPEND_ONLY:
        _run(f"""
            CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON evidence.{t}
                FOR EACH ROW EXECUTE FUNCTION evidence.uat_append_only();
        """)
    for t in TABLES:
        _run(f"""
            ALTER TABLE evidence.{t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE evidence.{t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON evidence.{t} USING {RULE} WITH CHECK {RULE};
        """)


def downgrade() -> None:
    for t in reversed(TABLES):
        _run(f"DROP TABLE evidence.{t} CASCADE;")
    _run("""
        DROP FUNCTION evidence.uat_append_only();
        DROP FUNCTION evidence.uat_fixture_locked();
        DROP FUNCTION evidence.uat_run_frozen();
        DROP FUNCTION evidence.uat_candidate_guard();
    """)
