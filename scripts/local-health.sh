#!/usr/bin/env sh
set -eu

services='postgres redpanda redis minio clickhouse otel-collector'
for service in $services; do
  state="$(docker compose ps --format json "$service" | tr -d '\n')"
  case "$state" in
    *'"health":"healthy"'*|*'"health":""'*'"state":"running"'*)
      printf '%s: healthy\n' "$service"
      ;;
    *)
      printf '%s: unhealthy\n' "$service" >&2
      docker compose ps "$service" >&2 || true
      exit 1
      ;;
  esac
done
