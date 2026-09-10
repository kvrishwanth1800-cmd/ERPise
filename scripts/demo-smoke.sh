#!/usr/bin/env sh
set -eu
base=${DEMO_API_URL:-http://localhost:8080}
cookie=$(mktemp)
cleanup() { rm -f "$cookie"; }
trap cleanup EXIT
curl --fail --silent "$base/health/live" >/dev/null
curl --fail --silent --show-error -c "$cookie" -X POST "$base/api/auth/login" \
  -H 'content-type: application/json' \
  -d '{"email":"demo@erpise.local","password":"demo-only-password"}' >/dev/null
curl --fail --silent -b "$cookie" "$base/api/session" >/dev/null
curl --fail --silent -b "$cookie" "$base/api/products" >/dev/null
curl --fail --silent -b "$cookie" -X POST "$base/api/consent" \
  -H 'content-type: application/json' -d '{"customer_id":"customer"}' >/dev/null
order=$(curl --fail --silent -b "$cookie" -X POST "$base/api/orders" \
  -H 'content-type: application/json' -H 'Idempotency-Key: smoke-order-001' \
  -d '{"product_id":"coffee","quantity":1,"customer_id":"customer","fulfillment_method":"pickup"}')
printf '%s' "$order" | grep 'reservation' >/dev/null
attempt=0
while ! curl --fail --silent -b "$cookie" "$base/api/orders" | grep 'smoke-order-001\|order-' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 20 ] || exit 1
  sleep 1
done
curl --fail --silent -b "$cookie" "$base/api/reports/sales" >/dev/null
curl --fail --silent -b "$cookie" -X POST "$base/api/auth/logout" >/dev/null
if curl --silent -b "$cookie" "$base/api/products" | grep -q authentication_required; then
  printf '%s\n' 'Demo integration smoke test passed'
else
  exit 1
fi
