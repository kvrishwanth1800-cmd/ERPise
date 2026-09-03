# Local development

## Prerequisites

Install Docker Desktop with Docker Compose v2. On Windows, use the WSL 2 backend and run these commands from a WSL distribution or a shell that can execute `sh` scripts.

## Start the local stack

1. Copy `.env.example` to `.env`.
2. Replace the local-only placeholder values. Do not add production credentials.
3. Run `make local-up`.

The command starts PostgreSQL, Redpanda, Redis, MinIO, ClickHouse, and the OpenTelemetry Collector. Data persists in Docker named volumes until `make local-down` followed by explicit Docker volume removal.

## Check health

Run `make local-health`. The script reports a health result for every required dependency and exits non-zero if a dependency is unavailable.

## Stop the local stack

Run `make local-down`. This stops services and preserves local named volumes.

## Security

`.env` is ignored by Git. Use only local development values. Never place production credentials, payment data, or customer data in this stack.
