CREATE TABLE engagement_conversation_messages (
    message_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    customer_id TEXT NOT NULL,
    order_id TEXT,
    body TEXT NOT NULL,
    UNIQUE (tenant_id, conversation_id, sequence)
);

CREATE TABLE engagement_cases (
    case_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    order_id TEXT,
    assignee_id TEXT,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    target_sequence BIGINT NOT NULL,
    escalation TEXT
);

CREATE TABLE engagement_campaigns (
    campaign_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    published_by TEXT,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE engagement_campaign_audience (
    campaign_id TEXT NOT NULL REFERENCES engagement_campaigns(campaign_id),
    tenant_id TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    PRIMARY KEY (campaign_id, contact_id)
);

CREATE TABLE engagement_outbox (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- DOWN
DROP TABLE engagement_outbox;
DROP TABLE engagement_campaign_audience;
DROP TABLE engagement_campaigns;
DROP TABLE engagement_cases;
DROP TABLE engagement_conversation_messages;
