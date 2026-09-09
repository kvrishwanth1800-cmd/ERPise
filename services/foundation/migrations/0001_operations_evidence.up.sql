CREATE TABLE operations_evidence (
  evidence_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  event_id TEXT,
  event_type TEXT,
  event_version TEXT,
  outcome TEXT NOT NULL,
  details JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX operations_evidence_tenant_trace_idx
  ON operations_evidence (tenant_id, trace_id, occurred_at DESC);
CREATE INDEX operations_evidence_tenant_event_idx
  ON operations_evidence (tenant_id, event_id, occurred_at DESC)
  WHERE event_id IS NOT NULL;

CREATE TABLE operations_alerts (
  alert_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  objective_name TEXT NOT NULL,
  observed_value DOUBLE PRECISION NOT NULL,
  threshold_value DOUBLE PRECISION NOT NULL,
  trace_id TEXT NOT NULL,
  status TEXT NOT NULL,
  action TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX operations_alerts_tenant_status_idx
  ON operations_alerts (tenant_id, status, occurred_at DESC);
