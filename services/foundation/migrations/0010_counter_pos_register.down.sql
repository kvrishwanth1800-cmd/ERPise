BEGIN;

DROP TABLE pos_returns;
DROP TABLE return_authorizations;
DROP TABLE pos_sales;
DROP TABLE register_shifts;
DROP FUNCTION IF EXISTS reject_pos_fact_mutation();

COMMIT;
