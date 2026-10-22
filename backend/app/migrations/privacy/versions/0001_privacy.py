"""Privacy control plane: purposes, processing activities, role map, consent, preferences,
restrictions, rights requests, retention policies and the personal-data map (ZID-PRIV-001 §1-19;
DEC-F3; ZID-DATA-001 §7.2, §9.1; CR-013).

Revision ID: privacy_0001
Revises:
Create Date: 2026-10-21

- purpose / processing_activity / retention_policy are versioned: a version is immutable once
  ACTIVE (a change is a new version; history is never rewritten, PRIV-001 §3, DATA §7.2).
- processing_role_assignment is the DEC-F3 role map record; an activity cannot become ACTIVE
  without an APPROVED assignment (enforced by trigger, not only in code).
- consent_receipt: a terminal consent can never become ACTIVE again (PRIV-03); renewal is a new row.
- data_map_entry: where a subject's personal data lives, which purpose and retention policy govern
  it, and when it is due (the subject-location index of PRIV-001 §10, §17). due_at is fixed from
  the original trigger, so a released hold never restarts the clock (PRIV-08).
All tables are tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "privacy_0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "purpose",
    "processing_activity",
    "processing_role_assignment",
    "consent_receipt",
    "preference",
    "processing_restriction",
    "rights_request",
    "retention_policy",
    "data_map_entry",
)

CODE = r"'^[A-Z][A-Z0-9_]{1,63}$'"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE privacy.purpose (
            purpose_id                   uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            purpose_code                 text NOT NULL CHECK (purpose_code ~ {CODE}),
            purpose_version              integer NOT NULL CHECK (purpose_version >= 1),
            description                  text NOT NULL,
            allowed_data_categories      text[] NOT NULL,
            allowed_recipient_classes    text[] NOT NULL DEFAULT '{{}}',
            compatible_secondary_purposes text[] NOT NULL DEFAULT '{{}}',
            prohibited_uses              text[] NOT NULL DEFAULT '{{}}',
            retention_policy_code        text,
            status                       text NOT NULL DEFAULT 'DRAFT'
                                         CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED')),
            created_by                   uuid NOT NULL,
            approved_by                  uuid,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            activated_at                 timestamptz,
            UNIQUE (tenant_id, purpose_code, purpose_version),
            CHECK (allowed_data_categories <@ ARRAY['D0','D1','D2','D3','D4']::text[]),
            CHECK (status = 'DRAFT' OR approved_by IS NOT NULL),
            CHECK (approved_by IS NULL OR approved_by <> created_by)
        );
        CREATE UNIQUE INDEX purpose_one_active ON privacy.purpose (tenant_id, purpose_code)
            WHERE status = 'ACTIVE';

        CREATE TABLE privacy.processing_activity (
            processing_activity_id       uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            activity_code                text NOT NULL CHECK (activity_code ~ {CODE}),
            version                      integer NOT NULL CHECK (version >= 1),
            name                         text NOT NULL,
            purpose_codes                text[] NOT NULL CHECK (cardinality(purpose_codes) > 0),
            data_categories              text[] NOT NULL,
            data_subject_classes         text[] NOT NULL DEFAULT '{{}}',
            lawful_basis                 text NOT NULL CHECK (lawful_basis IN ('CONSENT', 'CONTRACT',
                                         'LEGAL_OBLIGATION', 'LEGITIMATE_INTERESTS', 'PUBLIC_TASK',
                                         'VITAL_INTERESTS', 'US_STATE_PERMISSION')),
            special_category_condition   text,
            source_classes               text[] NOT NULL DEFAULT '{{}}',
            recipient_classes            text[] NOT NULL DEFAULT '{{}}',
            retention_policy_code        text,
            residency_profile            text,
            jurisdiction_pack_id         text NOT NULL,
            dpia_ref                     text,
            launch_flow                  boolean NOT NULL DEFAULT false,
            status                       text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT',
                                         'APPROVED', 'ACTIVE', 'RETIRED')),
            created_by                   uuid NOT NULL,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            activated_at                 timestamptz,
            UNIQUE (tenant_id, activity_code, version),
            CHECK (data_categories <@ ARRAY['D0','D1','D2','D3','D4']::text[]),
            CHECK (NOT ('D3' = ANY (data_categories)) OR dpia_ref IS NOT NULL OR status = 'DRAFT')
        );
        CREATE UNIQUE INDEX processing_activity_one_active ON privacy.processing_activity
            (tenant_id, activity_code) WHERE status = 'ACTIVE';

        CREATE TABLE privacy.processing_role_assignment (
            assignment_id                uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            processing_activity_id       uuid NOT NULL REFERENCES privacy.processing_activity,
            data_controller_principal_id uuid NOT NULL,
            zoiko_role                   text NOT NULL CHECK (zoiko_role IN ('CONTROLLER',
                                         'PROCESSOR', 'JOINT_CONTROLLER', 'INDEPENDENT_CONTROLLER')),
            processor_principal_ids      uuid[] NOT NULL DEFAULT '{{}}',
            recipient_classes            text[] NOT NULL DEFAULT '{{}}',
            jurisdiction_pack_id         text NOT NULL,
            contract_or_dpa_ref          text,
            effective_from               timestamptz NOT NULL,
            valid_to                     timestamptz,
            approval_ref                 text NOT NULL,
            created_by                   uuid NOT NULL,
            approved_by                  uuid NOT NULL,
            status                       text NOT NULL DEFAULT 'APPROVED'
                                         CHECK (status IN ('APPROVED', 'SUPERSEDED', 'RETIRED')),
            created_at                   timestamptz NOT NULL DEFAULT now(),
            CHECK (approved_by <> created_by),
            CHECK (zoiko_role <> 'PROCESSOR' OR contract_or_dpa_ref IS NOT NULL),
            CHECK (valid_to IS NULL OR valid_to > effective_from)
        );
        CREATE INDEX role_assignment_activity ON privacy.processing_role_assignment
            (tenant_id, processing_activity_id);

        CREATE TABLE privacy.consent_receipt (
            consent_id                   uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            subject_principal_id         uuid NOT NULL,
            controller_principal_id      uuid NOT NULL,
            recipient_principal_id       uuid,
            purpose_code                 text NOT NULL,
            purpose_version              integer NOT NULL,
            data_categories              text[] NOT NULL,
            processing_operations        text[] NOT NULL,
            notice_version               text NOT NULL,
            jurisdiction_pack_id         text NOT NULL,
            capture_method               text NOT NULL CHECK (capture_method IN ('WEB', 'MOBILE',
                                         'API', 'PAPER_VERIFIED', 'OTHER')),
            affirmative_action           text NOT NULL,
            status                       text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE',
                                         'WITHDRAWN', 'EXPIRED', 'SUPERSEDED', 'INVALIDATED')),
            granted_at                   timestamptz NOT NULL,
            expires_at                   timestamptz,
            withdrawn_at                 timestamptz,
            withdrawal_reason            text,
            evidence_receipt_id          uuid,
            receipt_hash                 text NOT NULL,
            CHECK ((status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL))
        );
        CREATE INDEX consent_subject ON privacy.consent_receipt
            (tenant_id, subject_principal_id, purpose_code);

        CREATE FUNCTION privacy.consent_terminal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'ACTIVE' THEN
                RAISE EXCEPTION 'privacy.consent_receipt %: a % consent is terminal (PRIV-03)',
                    OLD.consent_id, OLD.status USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - ARRAY['status', 'withdrawn_at', 'withdrawal_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'withdrawn_at', 'withdrawal_reason']) THEN
                RAISE EXCEPTION 'privacy.consent_receipt: consent evidence is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER consent_terminal BEFORE UPDATE ON privacy.consent_receipt
            FOR EACH ROW EXECUTE FUNCTION privacy.consent_terminal();
        CREATE TRIGGER consent_no_delete BEFORE DELETE ON privacy.consent_receipt
            FOR EACH ROW EXECUTE FUNCTION privacy.consent_terminal();

        CREATE TABLE privacy.preference (
            tenant_id                    uuid NOT NULL,
            subject_principal_id         uuid NOT NULL,
            preference_type              text NOT NULL CHECK (preference_type IN
                                         ('OPT_OUT_SALE_SHARING', 'LIMIT_SENSITIVE_PI',
                                         'DIRECT_MARKETING_OPT_OUT', 'ADMT_OPT_OUT')),
            value                        boolean NOT NULL,
            source                       text NOT NULL CHECK (source IN ('USER', 'GPC_SIGNAL',
                                         'AUTHORIZED_AGENT', 'RIGHTS_REQUEST')),
            revision                     integer NOT NULL DEFAULT 1,
            updated_at                   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, subject_principal_id, preference_type)
        );

        CREATE TABLE privacy.processing_restriction (
            restriction_id               uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            subject_principal_id         uuid NOT NULL,
            kind                         text NOT NULL CHECK (kind IN ('RESTRICTION', 'OBJECTION')),
            purpose_code                 text,
            reason_code                  text NOT NULL,
            rights_request_id            uuid,
            active                       boolean NOT NULL DEFAULT true,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            lifted_at                    timestamptz,
            lifted_by                    uuid,
            CHECK (active = (lifted_at IS NULL))
        );
        CREATE INDEX restriction_subject ON privacy.processing_restriction
            (tenant_id, subject_principal_id) WHERE active;

        CREATE TABLE privacy.rights_request (
            request_id                   uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            request_type                 text NOT NULL CHECK (request_type IN ('ACCESS',
                                         'RECTIFICATION', 'ERASURE', 'RESTRICTION', 'OBJECTION',
                                         'PORTABILITY', 'OPT_OUT_SALE_SHARING',
                                         'LIMIT_SENSITIVE_PI', 'ADMT_ACCESS', 'ADMT_OPT_OUT',
                                         'COMPLAINT', 'HUMAN_REVIEW')),
            subject_principal_id         uuid NOT NULL,
            requester_principal_id       uuid,
            authorized_agent_ref         text,
            jurisdiction_pack_id         text NOT NULL,
            received_at                  timestamptz NOT NULL,
            deadline_at                  timestamptz,
            deadline_source              text NOT NULL,
            identity_verification_state  text NOT NULL CHECK (identity_verification_state IN
                                         ('NOT_REQUIRED', 'PENDING', 'VERIFIED', 'FAILED')),
            verification_level           smallint NOT NULL DEFAULT 0,
            status                       text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN
                                         ('RECEIVED', 'VERIFYING', 'SCOPING', 'FULFILLING',
                                         'REVIEW', 'COMPLETED', 'PARTIALLY_DENIED', 'DENIED',
                                         'WITHDRAWN')),
            scope                        jsonb NOT NULL DEFAULT '{{}}',
            exemptions                   jsonb NOT NULL DEFAULT '[]',
            fulfilment_items             jsonb NOT NULL DEFAULT '[]',
            response_package_ref         uuid,
            linked_request_id            uuid,
            version                      integer NOT NULL DEFAULT 1,
            updated_at                   timestamptz NOT NULL DEFAULT now(),
            completed_at                 timestamptz
        );
        CREATE INDEX rights_request_open ON privacy.rights_request (tenant_id, deadline_at)
            WHERE status NOT IN ('COMPLETED', 'PARTIALLY_DENIED', 'DENIED', 'WITHDRAWN');

        CREATE TABLE privacy.retention_policy (
            retention_policy_id          uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            policy_code                  text NOT NULL CHECK (policy_code ~ {CODE}),
            version                      integer NOT NULL CHECK (version >= 1),
            object_type                  text NOT NULL,
            purpose_code                 text,
            jurisdiction_pack_id         text NOT NULL,
            retention_trigger            text NOT NULL CHECK (retention_trigger IN ('CREATED_AT',
                                         'LAST_USED', 'VERIFIED_AT', 'CONTRACT_END',
                                         'ACCOUNT_CLOSE', 'GRANT_EXPIRY', 'EVENT_DATE')),
            retention_days               integer NOT NULL CHECK (retention_days >= 0),
            action_on_expiry             text NOT NULL CHECK (action_on_expiry IN ('DELETE',
                                         'CRYPTO_SHRED', 'ANONYMIZE', 'ARCHIVE_RESTRICTED')),
            backup_erasure_window_days   integer CHECK (backup_erasure_window_days >= 0),
            legal_hold_suspends          boolean NOT NULL DEFAULT true,
            legally_required             boolean NOT NULL DEFAULT false,
            status                       text NOT NULL DEFAULT 'ACTIVE'
                                         CHECK (status IN ('ACTIVE', 'RETIRED')),
            created_by                   uuid NOT NULL,
            approved_by                  uuid NOT NULL,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, policy_code, version),
            CHECK (approved_by <> created_by)
        );
        CREATE UNIQUE INDEX retention_policy_one_active ON privacy.retention_policy
            (tenant_id, policy_code) WHERE status = 'ACTIVE';

        CREATE TABLE privacy.data_map_entry (
            entry_id                     uuid PRIMARY KEY,
            tenant_id                    uuid NOT NULL,
            subject_principal_id         uuid NOT NULL,
            store                        text NOT NULL CHECK (store ~ '^[a-z_]+\\.[a-z_]+$'),
            object_type                  text NOT NULL,
            object_ref                   text NOT NULL,
            purpose_code                 text NOT NULL,
            data_category                text NOT NULL CHECK (data_category IN
                                         ('D0','D1','D2','D3','D4')),
            retention_policy_code        text,
            processor_id                 text,
            region                       text,
            trigger_at                   timestamptz NOT NULL,
            due_at                       timestamptz,
            status                       text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE',
                                         'HELD', 'RESTRICTED', 'DISPOSED')),
            disposal_action              text,
            disposed_at                  timestamptz,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, store, object_ref),
            CHECK ((status = 'DISPOSED') = (disposed_at IS NOT NULL))
        );
        CREATE INDEX data_map_subject ON privacy.data_map_entry (tenant_id, subject_principal_id);
        CREATE INDEX data_map_due ON privacy.data_map_entry (due_at)
            WHERE status IN ('ACTIVE', 'HELD');

        -- DEC-F3 engineering enforcement: no ACTIVE processing activity without an approved role
        CREATE FUNCTION privacy.require_role_assignment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status = 'ACTIVE' AND NOT EXISTS (
                SELECT 1 FROM privacy.processing_role_assignment r
                WHERE r.processing_activity_id = NEW.processing_activity_id
                  AND r.status = 'APPROVED' AND r.effective_from <= clock_timestamp()
                  AND (r.valid_to IS NULL OR r.valid_to > clock_timestamp())) THEN
                RAISE EXCEPTION 'processing activity % has no approved role assignment (DEC-F3)',
                    NEW.activity_code USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER processing_activity_role BEFORE INSERT OR UPDATE OF status
            ON privacy.processing_activity
            FOR EACH ROW EXECUTE FUNCTION privacy.require_role_assignment();

        -- An ACTIVE version is immutable: a change is a new version (PRIV-001 §3; DATA §7.2).
        CREATE FUNCTION privacy.active_is_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'ACTIVE' AND (to_jsonb(NEW) - 'status') <> (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION '%: an ACTIVE version is immutable; publish a new version',
                    TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('ACTIVE', 'RETIRED') AND NEW.status NOT IN ('ACTIVE', 'RETIRED') THEN
                RAISE EXCEPTION '%: status cannot move back to %', TG_TABLE_NAME, NEW.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
                RAISE EXCEPTION '%: a retired version stays retired', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER purpose_immutable BEFORE UPDATE ON privacy.purpose
            FOR EACH ROW EXECUTE FUNCTION privacy.active_is_immutable();
        CREATE TRIGGER processing_activity_immutable BEFORE UPDATE ON privacy.processing_activity
            FOR EACH ROW EXECUTE FUNCTION privacy.active_is_immutable();
        CREATE TRIGGER retention_policy_immutable BEFORE UPDATE ON privacy.retention_policy
            FOR EACH ROW EXECUTE FUNCTION privacy.active_is_immutable();
    """)
    for table in TABLES:
        _run(f"""
            ALTER TABLE privacy.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE privacy.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON privacy.{table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        """)


def downgrade() -> None:
    _run("""
        DROP TABLE privacy.data_map_entry;
        DROP TABLE privacy.retention_policy;
        DROP TABLE privacy.rights_request;
        DROP TABLE privacy.processing_restriction;
        DROP TABLE privacy.preference;
        DROP TABLE privacy.consent_receipt;
        DROP FUNCTION privacy.consent_terminal();
        DROP TABLE privacy.processing_role_assignment;
        DROP TABLE privacy.processing_activity;
        DROP TABLE privacy.purpose;
        DROP FUNCTION privacy.require_role_assignment();
        DROP FUNCTION privacy.active_is_immutable();
    """)
