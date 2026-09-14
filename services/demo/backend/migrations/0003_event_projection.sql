CREATE TABLE IF NOT EXISTS demo_processed_events (
  event_id TEXT PRIMARY KEY,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS demo_event_projection (
  tenant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  fulfillment_status TEXT NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, order_id)
);
