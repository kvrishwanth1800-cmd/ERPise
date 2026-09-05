# Recovery drills

Run these drills only against the local validation stack or an approved isolated environment. Do not run migration down scripts against production.

## Start the validation stack

```sh
cp .env.example .env
docker compose --env-file .env up --detach --wait --wait-timeout 180
```

## Run recovery drills

```sh
TEST_DATABASE_URL=postgresql://erpise:change-me-local-only@localhost:5432/erpise \
TEST_REDPANDA_BOOTSTRAP_SERVERS=localhost:19092 \
uv run pytest services/foundation/tests/test_recovery_drills.py \
  services/foundation/tests/test_operations.py \
  services/foundation/tests/test_durable_outbox.py \
  services/foundation/tests/test_redpanda_outbox.py
cargo test -p edge-sync -p edge-sync-reconciliation
```

The drill suite verifies database restore from captured tenant settings after a full migration rollback and reapply cycle, tenant isolation after restore, broker retry and restart recovery, replay and duplicate safety, telemetry redaction, trace-to-event evidence, actionable alert creation, and offline queue recovery.

## Recovery response

1. Confirm the affected tenant and trace identifier before replay or restore.
2. For broker failure, restore connectivity and run the outbox publisher. The publisher retries durable pending records. Inspect dead letters before manual handling.
3. For migration rollback, use only the ordered down scripts in a disposable environment. Reapply the ordered up scripts, restore approved backup data, then verify tenant-scoped service behavior.
4. Record the restore outcome and trace identifier through the operations evidence service. If either data or behavior is not restored, keep the exercise outcome as failed and escalate.
5. Do not include passwords, tokens, authorization values, or payment card data in recovery evidence.

## Stop the stack

```sh
docker compose --env-file .env down --volumes
```
