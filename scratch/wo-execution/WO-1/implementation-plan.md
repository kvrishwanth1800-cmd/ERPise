# WO-1 implementation plan

## Scope
Create a pinned Docker Compose local dependency stack, non-secret configuration template, health checks, validation workflow, and Windows setup documentation.

## Affected files
- compose.yaml
- .env.example
- infrastructure/local/otel-collector.yaml
- scripts/local-up.sh
- scripts/local-health.sh
- Makefile
- .github/workflows/foundation.yml
- docs/runbooks/local-development.md

## Risks and rollback
Pinned images can change availability or health-command behavior. Roll back by deleting this feature branch or reverting this focused commit. Local data stays in named volumes and is never production data.
