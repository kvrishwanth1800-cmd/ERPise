#!/usr/bin/env sh
set -eu

max_attempts="${LOCAL_HEALTH_MAX_ATTEMPTS:-30}"
retry_seconds="${LOCAL_HEALTH_RETRY_SECONDS:-2}"
services="postgres redpanda redis minio clickhouse otel-collector"

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Docker is required to inspect local dependency health.' >&2
  exit 1
fi

inspect_service() {
  service="$1"
  container_id="$(docker compose ps -q "$service")"

  if [ -z "$container_id" ]; then
    printf '%s\n' 'missing'
    return 0
  fi

  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || printf '%s\n' 'missing'
}

report_service_failure() {
  service="$1"
  status="$2"

  printf '%s\n' "$service: $status" >&2
  printf '%s\n' "Recent logs for $service:" >&2
  docker compose logs --tail 80 --no-color "$service" >&2 || true
}

for service in $services; do
  attempt=1

  while [ "$attempt" -le "$max_attempts" ]; do
    status="$(inspect_service "$service")"

    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      printf '%s: healthy\n' "$service"
      break
    fi

    if [ "$attempt" -eq "$max_attempts" ]; then
      report_service_failure "$service" "$status"
      exit 1
    fi

    printf '%s: waiting for health (%s/%s, current: %s)\n' "$service" "$attempt" "$max_attempts" "$status"
    sleep "$retry_seconds"
    attempt=$((attempt + 1))
  done
done
