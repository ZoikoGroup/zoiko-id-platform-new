"""Email delivery log, suppression, marketing consent and contacts (Communication Estate §03, §05).

Revision ID: communication_0001
Revises:
Create Date: 2026-10-25

- `platform.email_message`: one row per (tenant, event, template, recipient). The unique key is the
  event-level idempotency of §03: redelivering a domain event never sends a second email. A row carries the
  template id and content version, the class, the delivery status and a reason code, and a keyed hash of the
  recipient. It never holds an address, a subject, a body, a merge value, a token or a code (OBSERVABILITY).
- `platform.email_suppression` and `platform.email_consent`: global, keyed by the recipient hash, because a
  bounce or an unsubscribe applies to the address and not to one tenant. The unsubscribe endpoint is public
  and has no tenant context.
- `platform.email_contact`: where a tenant's mail goes. The address itself lives in the evidence vault,
  encrypted under the subject's erasable key (principal contacts) or the cell data key (organization
  contacts); this table keeps the vault object id and the recipient hash.
Message and contact rows are tenant-scoped under RLS.
"""

from zid_platform.migrations import execute_script as _run

revision = "communication_0001"
down_revision = None
branch_labels = None
depends_on = None

CLASSES = "('SECURITY', 'SERVICE', 'PRIVACY', 'INCIDENT', 'MARKETING')"
STATUSES = "('PENDING', 'SENT', 'FAILED', 'SUPPRESSED', 'THROTTLED', 'RENDER_FAILED', 'DISABLED')"


def upgrade() -> None:
    _run(f"""
        CREATE TABLE platform.email_message (
            message_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            template_id      text NOT NULL CHECK (template_id ~ '^ZID-EM-[0-9]{{3}}$'),
            template_version text NOT NULL CHECK (length(template_version) BETWEEN 3 AND 40),
            message_class    text NOT NULL CHECK (message_class IN {CLASSES}),
            event_id         text NOT NULL CHECK (length(event_id) BETWEEN 1 AND 200),
            event_type       text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 200),
            recipient_hash   text NOT NULL CHECK (length(recipient_hash) BETWEEN 20 AND 128),
            status           text NOT NULL DEFAULT 'PENDING' CHECK (status IN {STATUSES}),
            reason           text CHECK (reason IS NULL OR length(reason) <= 120),
            attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            provider_ref     text,
            correlation_id   text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            sent_at          timestamptz,
            UNIQUE (tenant_id, event_id, template_id, recipient_hash),
            CHECK (status <> 'SENT' OR sent_at IS NOT NULL)
        );
        CREATE INDEX email_message_recent ON platform.email_message (tenant_id, recipient_hash, created_at DESC);

        CREATE TABLE platform.email_suppression (
            recipient_hash text NOT NULL CHECK (length(recipient_hash) BETWEEN 20 AND 128),
            reason         text NOT NULL CHECK (reason IN ('BOUNCE_HARD', 'COMPLAINT', 'UNSUBSCRIBED')),
            list_id        text NOT NULL DEFAULT '' CHECK (length(list_id) <= 80),
            source         text NOT NULL CHECK (length(source) BETWEEN 2 AND 80),
            created_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (recipient_hash, reason, list_id)
        );

        CREATE TABLE platform.email_consent (
            recipient_hash text NOT NULL CHECK (length(recipient_hash) BETWEEN 20 AND 128),
            list_id        text NOT NULL CHECK (length(list_id) BETWEEN 1 AND 80),
            status         text NOT NULL CHECK (status IN ('GRANTED', 'WITHDRAWN')),
            basis_public   text NOT NULL CHECK (length(basis_public) BETWEEN 3 AND 300),
            recorded_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (recipient_hash, list_id)
        );

        CREATE TABLE platform.email_contact (
            contact_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            purpose         text NOT NULL CHECK (purpose IN ('PRINCIPAL', 'OWNER', 'BILLING', 'SECURITY', 'TECHNICAL')),
            principal_id    uuid,
            address_hash    text NOT NULL CHECK (length(address_hash) BETWEEN 20 AND 128),
            vault_object_id uuid NOT NULL,
            created_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            superseded_at   timestamptz,
            CHECK ((purpose = 'PRINCIPAL') = (principal_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX email_contact_one_principal ON platform.email_contact (tenant_id, principal_id)
            WHERE superseded_at IS NULL AND principal_id IS NOT NULL;
        CREATE UNIQUE INDEX email_contact_one_role_address ON platform.email_contact (tenant_id, purpose, address_hash)
            WHERE superseded_at IS NULL AND principal_id IS NULL;
        CREATE INDEX email_contact_by_purpose ON platform.email_contact (tenant_id, purpose)
            WHERE superseded_at IS NULL;

        ALTER TABLE platform.email_message ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.email_message FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.email_message
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        ALTER TABLE platform.email_contact ENABLE ROW LEVEL SECURITY;
        ALTER TABLE platform.email_contact FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON platform.email_contact
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    _run("""
        DROP TABLE platform.email_contact;
        DROP TABLE platform.email_consent;
        DROP TABLE platform.email_suppression;
        DROP TABLE platform.email_message;
    """)
