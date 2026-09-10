#!/usr/bin/env sh
set -eu
base=${DEMO_API_URL:-http://localhost:8080}
for path in /health/live /health/ready /api/demo /api/products /api/reports/sales; do curl --fail --silent "$base$path" >/dev/null; done
for path in consent orders messages cases; do curl --fail --silent -X POST "$base/api/$path" -H 'content-type: application/json' -d '{}' >/dev/null; done
printf '%s\n' 'Demo smoke test passed'
