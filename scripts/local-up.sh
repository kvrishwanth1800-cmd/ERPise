#!/usr/bin/env sh
set -eu

if [ ! -f .env ]; then
  printf '%s\n' 'Missing .env. Copy .env.example to .env and replace local-only placeholder values.' >&2
  exit 1
fi

docker compose --env-file .env up --detach --wait --wait-timeout 180
./scripts/local-health.sh
