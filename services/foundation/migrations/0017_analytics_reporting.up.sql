BEGIN;

CREATE TABLE analytics_projection_events (
    tenant_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    amount NUMERIC(24, 6) NOT NULL,
    currency TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    store_id TEXT,
    warehouse_id TEXT,
    channel TEXT,
    entity_id TEXT,
    PRIMARY KEY (tenant_id, source_event_id)
);
CREATE INDEX analytics_projection_events_query_idx ON analytics_projection_events
    (tenant_id, occurred_at, metric, currency);

CREATE TABLE analytics_projection_rebuilds (
    rebuild_id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    rebuilt_at TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, trace_id)
);

CREATE TABLE analytics_report_exports (
    tenant_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    filters JSONB NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, trace_id)
);

COMMIT;
