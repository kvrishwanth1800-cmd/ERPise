# ERPise Demonstration

**Classification:** DEMONSTRATION / PRE-PRODUCTION. Demo credentials and data are not for production use.

## Prerequisites

Install Docker Desktop on Windows or macOS, or Docker Engine with the Compose plugin on Linux. Ensure ports 3000, 5432, 8080, and 8090 are free.

## Start

Windows PowerShell:
```powershell
Copy-Item .env.demo.example .env.demo
docker compose --env-file .env.demo -f docker-compose.demo.yml up --build
```

macOS or Linux:
```sh
cp .env.demo.example .env.demo
docker compose --env-file .env.demo -f docker-compose.demo.yml up --build
```

Open http://localhost:3000. The API is at http://localhost:8080 and the edge readiness endpoint is http://localhost:8090.

## Demo access

Use `demo@erpise.local` with password `demo-only-password`. These values are demo-only.

## Demo sequence

Select the demo administrator, inspect the seeded tenant, store, warehouse, register, product, inventory, customer consent, and supplier. Submit consent, order, communication, and support-case API actions through the dashboard or API. Confirm sales reporting at `/api/reports/sales`.

## Health and smoke test

```sh
curl http://localhost:8080/health/ready
sh scripts/demo-smoke.sh
```

## Shutdown and reset

```sh
docker compose --env-file .env.demo -f docker-compose.demo.yml down
docker compose --env-file .env.demo -f docker-compose.demo.yml down --volumes
```

The second command removes demo data. If startup fails, run `docker compose --env-file .env.demo -f docker-compose.demo.yml logs` and verify Docker and required ports.
