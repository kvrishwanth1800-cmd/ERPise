.PHONY: local-up local-down local-health compose-validate

local-up:
	./scripts/local-up.sh

local-down:
	docker compose --env-file .env down

local-health:
	./scripts/local-health.sh

compose-validate:
	docker compose --env-file .env.example config --quiet
