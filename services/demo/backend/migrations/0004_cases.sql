CREATE TABLE IF NOT EXISTS demo_cases (
  case_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  subject TEXT NOT NULL,
  details TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'assigned', 'escalated', 'resolved')),
  opened_by TEXT NOT NULL,
  assignee TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS demo_cases_tenant_updated ON demo_cases (tenant_id, updated_at DESC);
