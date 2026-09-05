BEGIN;

CREATE TABLE durable_outbox_records (
  event_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ NULL,
  publish_attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NULL,
  locked_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX durable_outbox_pending_idx
  ON durable_outbox_records (occurred_at)
  WHERE published_at IS NULL;

CREATE TABLE outbox_dead_letters (
  dead_letter_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE REFERENCES durable_outbox_records(event_id) ON DELETE RESTRICT,
  tenant_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE consumer_event_progress (
  consumer_name TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES durable_outbox_records(event_id) ON DELETE RESTRICT,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  replayed BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (consumer_name, tenant_id, event_id)
);
CREATE INDEX consumer_event_progress_tenant_idx
  ON consumer_event_progress (consumer_name, tenant_id, processed_at);

CREATE TABLE replay_projection_effects (
  consumer_name TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  projection_key TEXT NOT NULL,
  logical_effect_count BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer_name, tenant_id, projection_key)
);

CREATE TABLE outbox_business_records (
  record_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
