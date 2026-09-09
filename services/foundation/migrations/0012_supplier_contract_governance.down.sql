BEGIN;

DROP TRIGGER supplier_certifications_no_delete ON supplier_certifications;
DROP TRIGGER supplier_contract_versions_no_delete ON supplier_contract_versions;
DROP TRIGGER supplier_contract_terms_immutable ON supplier_contract_versions;
DROP FUNCTION reject_supplier_fact_mutation();

ALTER TABLE suppliers DROP CONSTRAINT suppliers_active_bank_change_fk;

DROP TABLE supplier_certifications;
DROP TABLE supplier_contract_versions;
DROP TABLE supplier_bank_change_requests;
DROP TABLE suppliers;

COMMIT;
