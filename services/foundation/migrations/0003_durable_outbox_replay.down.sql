BEGIN;
DROP TABLE IF EXISTS replay_projection_effects;
DROP TABLE IF EXISTS consumer_event_progress;
DROP TABLE IF EXISTS outbox_dead_letters;
DROP TABLE IF EXISTS outbox_business_records;
DROP TABLE IF EXISTS durable_outbox_records;
COMMIT;
