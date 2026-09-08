BEGIN;

DROP TABLE payment_settlement_exceptions;
DROP TABLE payment_refunds;
DROP TABLE payment_transactions;
DROP TABLE payment_intents;
DROP FUNCTION IF EXISTS reject_payment_mutation();

COMMIT;
