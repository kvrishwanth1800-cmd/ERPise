.PHONY: local-up local-down local-health compose-validate demo demo-down demo-reset demo-smoke
local-up:
	./scripts/local-up.sh
local-down:
	docker compose --env-file .env down
local-health:
	./scripts/local-health.sh
compose-validate:
	docker compose --env-file .env.example config --quiet
demo:
	@test -f .env.demo || cp .env.demo.example .env.demo
	docker compose --env-file .env.demo -f docker-compose.demo.yml up --build
demo-down:
	docker compose --env-file .env.demo -f docker-compose.demo.yml down
demo-reset:
	docker compose --env-file .env.demo -f docker-compose.demo.yml down --volumes
demo-smoke:
	sh scripts/demo-smoke.sh
