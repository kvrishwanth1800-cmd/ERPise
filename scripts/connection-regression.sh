#!/usr/bin/env sh
set -eu
api=${DEMO_API_URL:-http://localhost:8080}
cookie=$(mktemp)
trap 'rm -f "$cookie"' EXIT
curl --fail --silent "$api/health/live" >/dev/null
curl --fail --silent -c "$cookie" -X POST "$api/api/auth/login" -H 'content-type: application/json' -d '{"email":"demo@erpise.local","password":"demo-only-password"}' >/dev/null
curl --fail --silent -b "$cookie" "$api/api/products" | grep 'coffee' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/consent" -H 'content-type: application/json' -d '{"customer_id":"customer"}' | grep '"consented": true' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/consent/revoke" -H 'content-type: application/json' -d '{"customer_id":"customer"}' | grep '"consented": false' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/consent" -H 'content-type: application/json' -d '{"customer_id":"customer"}' | grep '"consented": true' >/dev/null
order=$(curl --fail --silent -b "$cookie" -X POST "$api/api/orders" -H 'content-type: application/json' -H 'Idempotency-Key: connection-regression-001' -d '{"product_id":"coffee","quantity":1,"customer_id":"customer","fulfillment_method":"pickup"}')
printf '%s' "$order" | grep 'reserved' >/dev/null
for attempt in $(seq 1 20); do curl --fail --silent -b "$cookie" "$api/api/projections/orders" | grep 'order-' >/dev/null && break; [ "$attempt" -eq 20 ] && exit 1; sleep 1; done
curl --fail --silent -b "$cookie" -X POST "$api/api/events/replay" | grep 'replayed' >/dev/null
edge_id=edge-regression-001
curl --fail --silent -b "$cookie" -X POST "$api/api/edge/reserve" -H 'Idempotency-Key: edge-regression-001' -H 'content-type: application/json' -d "{\"reservation_id\":\"$edge_id\",\"quantity\":1}" | grep 'reserved' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/edge/confirm" -H 'content-type: application/json' -d "{\"reservation_id\":\"$edge_id\"}" | grep 'confirmed' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/edge/release" -H 'content-type: application/json' -d "{\"reservation_id\":\"$edge_id\"}" | grep 'released' >/dev/null
curl --fail --silent -b "$cookie" "$api/api/edge" | grep 'tenant_id' >/dev/null
case=$(curl --fail --silent -b "$cookie" -X POST "$api/api/cases" -H 'content-type: application/json' -d '{"subject":"Connection check","details":"Support workflow validation"}')
case_id=$(printf '%s' "$case" | sed -n 's/.*"case_id": "\([^"]*\)".*/\1/p')
[ -n "$case_id" ]
curl --fail --silent -b "$cookie" -X POST "$api/api/cases/$case_id" -H 'content-type: application/json' -d '{"status":"assigned","assignee":"demo@erpise.local"}' | grep 'assigned' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/cases/$case_id" -H 'content-type: application/json' -d '{"status":"escalated"}' | grep 'escalated' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/cases/$case_id" -H 'content-type: application/json' -d '{"status":"resolved"}' | grep 'resolved' >/dev/null
curl --fail --silent -b "$cookie" "$api/api/cases" | grep "$case_id" >/dev/null
curl --fail --silent -b "$cookie" "$api/api/reports/sales" | grep 'gross_sales_cents' >/dev/null
curl --fail --silent -b "$cookie" -X POST "$api/api/auth/logout" >/dev/null
curl --silent -b "$cookie" "$api/api/products" | grep 'authentication_required' >/dev/null
printf '%s\n' 'Connection regression passed'
