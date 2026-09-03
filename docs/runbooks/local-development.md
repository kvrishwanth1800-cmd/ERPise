# Local development

## Prerequisites

Install Docker Desktop with Docker Compose v2. On Windows, use the WSL 2 backend and run the shell commands from a WSL distribution. PowerShell commands are provided below for validation.

## Start the local stack

1. Copy `.env.example` to `.env`.
2. Replace the local-only placeholder values. Do not add production credentials.
3. Run `make local-up`.

The command starts PostgreSQL, Redpanda, Redis, MinIO, ClickHouse, and the OpenTelemetry Collector. Data persists in Docker named volumes until `make local-down` followed by explicit Docker volume removal.

## Check health

Run `make local-health`. The script waits with bounded retries, reports each dependency state, prints recent logs for any unhealthy dependency, and exits non-zero only after a dependency remains unhealthy after the configured retry limit.

## Windows PowerShell validation

Run the following from the repository root after Docker Desktop is running:

```powershell
Copy-Item .env.example .env
# Replace only local placeholder values in .env. Do not use production credentials.
docker compose --env-file .env config --quiet
docker compose --env-file .env up --detach --wait --wait-timeout 180
$services = 'postgres','redpanda','redis','minio','clickhouse','otel-collector'
foreach ($service in $services) {
  $id = docker compose ps -q $service
  if (-not $id) { throw "$service has no container" }
  $health = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $id
  if ($health -ne 'healthy' -and $health -ne 'running') {
    docker compose logs --tail 80 --no-color $service
    throw "$service is $health"
  }
  Write-Output "$service: healthy"
}
docker compose --env-file .env down --volumes
```

## Stop the local stack

Run `make local-down`. This stops services and preserves local named volumes.

## Security

`.env` is ignored by Git. Use only local development values. Never place production credentials, payment data, or customer data in this stack.
