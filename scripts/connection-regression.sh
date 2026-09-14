#!/usr/bin/env sh
set -eu
api=${DEMO_API_URL:-http://localhost:8080}
edge=${DEMO_EDGE_URL:-http://localhost:8090}
cookie=$(mktemp)
trap 'rm -f "$cookie"' EXIT
curl --fail --silent "$api/health/live" >/dev/null
curl --fail --silent -c "$cookie" -X POST "$api/api/auth/login" -H 'content-type: application/json' -d '{"email":"demo@erpise.local","password":"demo-only-password"}' >/dev/null
curl --fail --silent -b "$cookie" "$api/api/products" | grep 'coffee' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/consent" -H 'content-type: application/json' -d '{"customer_id":"customer"}' >/dev/null
order=$(curl --fail --silent -b "$cookie" -X POST "$api/api/orders" -H 'content-type: application/json' -H 'Idempotency-Key: connection-regression-001' -d '{"product_id":"coffee","quantity":1,"customer_id":"customer","fulfillment_method":"pickup"}')
printf '%s' "$order" | grep 'reserved' >/dev/null
for attempt in $(seq 1 20); do
  curl --fail --silent -b "$cookie" "$api/api/projections/orders" | grep 'order-' >/dev/null && break
  [ "$attempt" -eq 20 ] && exit 1
  sleep 1
done
curl --fail --silent -b "$cookie" -X POST "$api/api/events/replay" | grep 'replayed' >/dev/null
curl --fail --silent -X POST "$edge/reserve" -H 'X-Tenant-Id: demo-tenant' -H 'Idempotency-Key: edge-regression-001' -H 'content-type: application/json' -d '{"reservation_id":"edge-regression-001","quantity":1}' | grep 'reserved' >/dev/null
curl --fail --silent -X POST "$edge/confirm" -H 'X-Tenant-Id: demo-tenant' -H 'content-type: application/json' -d '{"reservation_id":"edge-regression-001"}' | grep 'confirmed' >/dev/null
curl --fail --silent -X POST "$edge/release" -H 'X-Tenant-Id: demo-tenant' -H 'content-type: application/json' -d '{"reservation_id":"edge-regression-001"}' | grep 'released' >/dev/null
curl --fail --silent -b "$cookie" "$api/api/reports/sales" | grep 'gross_sales_cents' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/auth/logout" >/dev/null
curl --silent -b "$cookie" "$api/api/products" | grep 'authentication_required' >/dev/null
printf '%s\n' 'Connection regression passed'
