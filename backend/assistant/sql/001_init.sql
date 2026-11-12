-- Public assistant schema (ZI-CHAT-ADR-006; PRI-009; INT-010). Applied by `python -m zid_assistant.migrate` as the OWNER role.
-- Rules enforced by the shape of these tables:
--   * no visitor chat text, no model output, no IP address, no session handle anywhere (RET-01, PR-02);
--   * analytics rows carry coarse metadata only (intent, verdict, reason, timings), never free text (ADR-12);
--   * intake contact data exists ONLY in intake_request.encrypted_fields, written after explicit consent (INV-05);
--   * outbox rows carry no personal data (INT-010 section 05).
CREATE SCHEMA IF NOT EXISTS public_assistant;

CREATE TABLE IF NOT EXISTS public_assistant.schema_version (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_assistant.feedback_event (
    feedback_id  uuid PRIMARY KEY,
    response_id  text NOT NULL CHECK (response_id ~ '^rsp_[0-9a-f]{32}$'),
    rating       text NOT NULL CHECK (rating IN ('USEFUL', 'NOT_USEFUL')),
    intent_id    text CHECK (intent_id ~ '^INT-[0-9]{2}$'),
    snapshot_id  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_assistant.message_metric (
    metric_id    uuid PRIMARY KEY,
    intent_id    text CHECK (intent_id ~ '^INT-[0-9]{2}$'),
    decision     text NOT NULL CHECK (decision IN ('ANSWER', 'CLARIFY', 'ABSTAIN', 'REFUSE', 'ESCALATE')),
    reason       text NOT NULL,
    route        text NOT NULL,
    ai_used      boolean NOT NULL DEFAULT false,
    latency_ms   integer NOT NULL CHECK (latency_ms >= 0),
    snapshot_id  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_assistant.safety_event (
    event_id    uuid PRIMARY KEY,
    category    text NOT NULL CHECK (length(category) BETWEEN 1 AND 40),
    route       text NOT NULL CHECK (length(route) BETWEEN 1 AND 40),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_assistant.audit_event (
    event_id        uuid PRIMARY KEY,
    event_type      text NOT NULL,
    purpose         text,
    policy_rev      text NOT NULL,
    correlation_id  text,
    outcome         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Intake: the only table that can hold personal data, and only after a separate, consented submission.
CREATE TABLE IF NOT EXISTS public_assistant.intake_request (
    request_id        uuid PRIMARY KEY,
    purpose           text NOT NULL CHECK (purpose IN ('SALES', 'SUPPORT')),
    country_code      text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    schema_rev        text NOT NULL,
    notice_rev        text NOT NULL,
    idempotency_key   text NOT NULL UNIQUE,
    payload_digest    text NOT NULL,
    encrypted_fields  bytea,                                   -- NULL after erasure
    reference         text NOT NULL UNIQUE,
    status            text NOT NULL CHECK (status IN ('ACCEPTED', 'QUEUED', 'DELIVERED', 'REJECTED', 'RETRYING', 'DEAD_LETTER', 'SUSPENDED', 'ERASURE_PENDING', 'ERASED')),
    retention_policy_ref text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_assistant.delivery_outbox (
    outbox_id        uuid PRIMARY KEY,
    request_id       uuid NOT NULL REFERENCES public_assistant.intake_request (request_id),
    target           text NOT NULL,
    adapter_rev      text NOT NULL,
    state            text NOT NULL CHECK (state IN ('PENDING', 'RETRYING', 'DELIVERED', 'REJECTED', 'DEAD_LETTER')),
    attempts         integer NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    last_error_code  text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS delivery_outbox_due ON public_assistant.delivery_outbox (next_attempt_at) WHERE state IN ('PENDING', 'RETRYING');

CREATE TABLE IF NOT EXISTS public_assistant.receiver_receipt (
    request_id        uuid PRIMARY KEY REFERENCES public_assistant.intake_request (request_id),
    recipient_ref     text NOT NULL,                           -- private: never shown to the visitor
    accepted_at       timestamptz NOT NULL,
    response_code     integer NOT NULL,
    correlation_id    text
);

CREATE TABLE IF NOT EXISTS public_assistant.marketing_evidence (
    evidence_id   uuid PRIMARY KEY,
    channel       text NOT NULL CHECK (channel IN ('EMAIL')),
    country_code  text NOT NULL CHECK (country_code ~ '^[A-Z]{2}$'),
    notice_rev    text NOT NULL,
    choice        boolean NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz
);
