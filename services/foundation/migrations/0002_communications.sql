CREATE TABLE communication_templates (
    tenant_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id, template_id, version)
);

CREATE TABLE communication_messages (
    message_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    template_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    failure_kind TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE communication_outbox (
    event_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES communication_messages(message_id),
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMPTZ
);

CREATE INDEX communication_message_tenant_status_idx
    ON communication_messages (tenant_id, status);

-- DOWN
DROP TABLE communication_outbox;
DROP TABLE communication_messages;
DROP TABLE communication_templates;
