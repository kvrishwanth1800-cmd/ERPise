BEGIN;

CREATE TABLE customers (
  tenant_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  PRIMARY KEY (tenant_id, customer_id)
);

CREATE TABLE customer_identity_history (
  tenant_id TEXT NOT NULL,
  history_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  related_customer_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('merged', 'unmerged')),
  occurred_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, history_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, customer_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id, related_customer_id) REFERENCES customers (tenant_id, customer_id) ON DELETE RESTRICT
);

CREATE TABLE consent_facts (
  tenant_id TEXT NOT NULL,
  consent_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('granted', 'withdrawn')),
  version TEXT NOT NULL,
  evidence TEXT NOT NULL,
  scope TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  replaces_consent_id TEXT,
  PRIMARY KEY (tenant_id, consent_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, customer_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id, replaces_consent_id) REFERENCES consent_facts (tenant_id, consent_id) ON DELETE RESTRICT
);
CREATE INDEX consent_facts_current_idx ON consent_facts (tenant_id, customer_id, purpose, occurred_at DESC, consent_id DESC);

CREATE TABLE privacy_requests (
  tenant_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  request_type TEXT NOT NULL CHECK (request_type IN ('export', 'deletion')),
  scope TEXT NOT NULL,
  legal_evidence TEXT NOT NULL,
  outcome TEXT CHECK (outcome IN ('exported', 'deleted', 'rejected')),
  PRIMARY KEY (tenant_id, request_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, customer_id) ON DELETE RESTRICT
);

CREATE TABLE stored_value_effects (
  tenant_id TEXT NOT NULL,
  effect_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  value_type TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount <> 0),
  reason TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  reversal_of_effect_id TEXT,
  PRIMARY KEY (tenant_id, effect_id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, customer_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id, reversal_of_effect_id) REFERENCES stored_value_effects (tenant_id, effect_id) ON DELETE RESTRICT
);
CREATE INDEX stored_value_effects_balance_idx ON stored_value_effects (tenant_id, customer_id, value_type, occurred_at, effect_id);

COMMIT;
