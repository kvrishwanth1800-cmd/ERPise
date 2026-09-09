BEGIN;

DROP TABLE order_history;
DROP TABLE order_refunds;
DROP TABLE order_returns;
DROP TABLE order_fulfillments;
DROP TABLE order_cancellations;
DROP TABLE order_substitutions;
DROP TABLE order_allocations;
DROP TABLE orders;
DROP FUNCTION IF EXISTS reject_order_line_mutation();
DROP FUNCTION IF EXISTS reject_order_fact_mutation();

COMMIT;
