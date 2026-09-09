BEGIN;

DROP TABLE IF EXISTS purchase_order_cancellations;
DROP TABLE IF EXISTS purchase_order_closures;
DROP TABLE IF EXISTS purchase_order_asns;
DROP TABLE IF EXISTS purchase_order_changes;
DROP TABLE IF EXISTS purchase_order_acknowledgments;
DROP TABLE IF EXISTS purchase_order_history;
DROP TABLE IF EXISTS purchase_orders;
DROP TRIGGER IF EXISTS awards_append_only_delete ON awards;
DROP TRIGGER IF EXISTS awards_append_only_update ON awards;
DROP FUNCTION IF EXISTS reject_procurement_fact_mutation();
DROP TABLE IF EXISTS awards;
DROP TABLE IF EXISTS quotes;
DROP TABLE IF EXISTS requisitions;

COMMIT;
