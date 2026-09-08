BEGIN;

CREATE TABLE suppliers (
  tenant_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'suspended', 'inactive')),
  responsible_organization_id TEXT NOT NULL,
  active_bank_change_id TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, supplier_id)
);
CREATE INDEX suppliers_responsible_idx ON suppliers (tenant_id, responsible_organization_id, status);

CREATE TABLE supplier_bank_change_requests (
  tenant_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  requester_id TEXT NOT NULL,
  account_holder TEXT NOT NULL,
  account_number TEXT NOT NULL,
  bank_identifier TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
  approver_id TEXT,
  requested_at TIMESTAMPTZ NOT NULL,
  decided_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, request_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, supplier_id) REFERENCES suppliers (tenant_id, supplier_id) ON DELETE RESTRICT,
  CONSTRAINT supplier_bank_change_separate_approver CHECK (approver_id IS NULL OR approver_id <> requester_id),
  CONSTRAINT supplier_bank_change_decision_complete CHECK (
    (status = 'pending' AND approver_id IS NULL AND decided_at IS NULL)
    OR (status <> 'pending' AND approver_id IS NOT NULL AND decided_at IS NOT NULL)
  )
);
CREATE INDEX supplier_bank_change_requests_supplier_idx ON supplier_bank_change_requests (tenant_id, supplier_id, status);

ALTER TABLE suppliers
  ADD CONSTRAINT suppliers_active_bank_change_fk
  FOREIGN KEY (tenant_id, active_bank_change_id)
  REFERENCES supplier_bank_change_requests (tenant_id, request_id) ON DELETE RESTRICT;

CREATE TABLE supplier_contract_versions (
  tenant_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  contract_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  version_number INTEGER NOT NULL CHECK (version_number > 0),
  terms JSONB NOT NULL,
  effective_from TIMESTAMPTZ NOT NULL,
  expires_on DATE,
  status TEXT NOT NULL CHECK (status IN ('registered', 'active', 'superseded')),
  PRIMARY KEY (tenant_id, version_id),
  UNIQUE (tenant_id, contract_id, version_number),
  UNIQUE (tenant_id, contract_id, effective_from),
  FOREIGN KEY (tenant_id, supplier_id) REFERENCES suppliers (tenant_id, supplier_id) ON DELETE RESTRICT
);
CREATE INDEX supplier_contract_versions_effective_idx ON supplier_contract_versions (tenant_id, contract_id, effective_from DESC, version_number DESC);
CREATE INDEX supplier_contract_versions_expiry_idx ON supplier_contract_versions (tenant_id, expires_on) WHERE expires_on IS NOT NULL;

CREATE TABLE supplier_certifications (
  tenant_id TEXT NOT NULL,
  certification_id TEXT NOT NULL,
  supplier_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  expires_on DATE NOT NULL,
  PRIMARY KEY (tenant_id, certification_id),
  FOREIGN KEY (tenant_id, supplier_id) REFERENCES suppliers (tenant_id, supplier_id) ON DELETE RESTRICT
);
CREATE INDEX supplier_certifications_expiry_idx ON supplier_certifications (tenant_id, expires_on);

CREATE OR REPLACE FUNCTION reject_supplier_fact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'supplier contract version and certification records are append-only'; END;
$$;
CREATE TRIGGER supplier_contract_terms_immutable BEFORE UPDATE ON supplier_contract_versions
  FOR EACH ROW WHEN (
    OLD.terms IS DISTINCT FROM NEW.terms
    OR OLD.effective_from IS DISTINCT FROM NEW.effective_from
    OR OLD.version_number IS DISTINCT FROM NEW.version_number
    OR OLD.contract_id IS DISTINCT FROM NEW.contract_id
  ) EXECUTE FUNCTION reject_supplier_fact_mutation();
CREATE TRIGGER supplier_contract_versions_no_delete BEFORE DELETE ON supplier_contract_versions
  FOR EACH ROW EXECUTE FUNCTION reject_supplier_fact_mutation();
CREATE TRIGGER supplier_certifications_no_delete BEFORE DELETE ON supplier_certifications
  FOR EACH ROW EXECUTE FUNCTION reject_supplier_fact_mutation();

COMMIT;
