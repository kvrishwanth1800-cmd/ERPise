BEGIN;

CREATE TABLE organizations (
  organization_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  parent_organization_id TEXT NULL REFERENCES organizations(organization_id) ON DELETE RESTRICT,
  settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (parent_organization_id IS NULL OR parent_organization_id <> organization_id)
);
CREATE INDEX organizations_tenant_parent_idx ON organizations (tenant_id, parent_organization_id);

CREATE TABLE organization_operational_dependencies (
  dependency_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX organization_dependencies_scope_idx ON organization_operational_dependencies (tenant_id, organization_id);

CREATE TABLE authorization_grants (
  grant_id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  action TEXT NOT NULL,
  organization_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  record_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  revoked_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX authorization_grants_scope_idx ON authorization_grants (tenant_id, principal_id, action) WHERE revoked_at IS NULL;

CREATE TABLE session_revocations (
  session_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE authorization_decisions (
  decision_id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  action TEXT NOT NULL,
  allowed BOOLEAN NOT NULL,
  trace_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX authorization_decisions_scope_idx ON authorization_decisions (tenant_id, trace_id, occurred_at DESC);

CREATE TABLE audit_records (
  audit_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  authority TEXT NOT NULL,
  source TEXT NOT NULL,
  reason TEXT NOT NULL,
  policy TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  result TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_records_scope_idx ON audit_records (tenant_id, trace_id, occurred_at DESC);
CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_records are append-only'; END; $$;
CREATE TRIGGER audit_records_immutable BEFORE UPDATE OR DELETE ON audit_records
  FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

CREATE TABLE approvals (
  approval_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  requester_id TEXT NOT NULL,
  policy TEXT NOT NULL,
  timeout_outcome TEXT NOT NULL CHECK (timeout_outcome IN ('retry', 'escalate', 'compensate')),
  status TEXT NOT NULL DEFAULT 'pending',
  resolver_id TEXT NULL,
  version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ NULL,
  CHECK (resolver_id IS NULL OR resolver_id <> requester_id)
);

CREATE TABLE hierarchy_outbox_records (
  event_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('created', 'updated', 'deleted')),
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  delivered_at TIMESTAMPTZ NULL
);
CREATE INDEX hierarchy_outbox_pending_idx ON hierarchy_outbox_records (occurred_at) WHERE delivered_at IS NULL;

CREATE OR REPLACE FUNCTION organization_scope_is_valid() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.parent_organization_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM organizations parent
    WHERE parent.organization_id = NEW.parent_organization_id AND parent.tenant_id = NEW.tenant_id
  ) THEN RAISE EXCEPTION 'parent organization is outside tenant scope'; END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER organizations_validate_parent BEFORE INSERT OR UPDATE ON organizations
  FOR EACH ROW EXECUTE FUNCTION organization_scope_is_valid();

CREATE OR REPLACE FUNCTION prevent_organization_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM organization_operational_dependencies d WHERE d.organization_id = OLD.organization_id)
     OR EXISTS (SELECT 1 FROM organizations child WHERE child.parent_organization_id = OLD.organization_id) THEN
    RAISE EXCEPTION 'organization has protected dependencies';
  END IF;
  RETURN OLD;
END; $$;
CREATE TRIGGER organizations_protect_delete BEFORE DELETE ON organizations
  FOR EACH ROW EXECUTE FUNCTION prevent_organization_deletion();

COMMIT;
