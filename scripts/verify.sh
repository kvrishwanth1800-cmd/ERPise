#!/usr/bin/env sh
set -eu

corepack enable
env -u CI -u GITHUB_ACTIONS -u BUILD_NUMBER -u RUN_ID -u CONTINUOUS_INTEGRATION \
  pnpm install --no-frozen-lockfile --config.frozen-lockfile=false
pnpm verify
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
uv sync --all-groups
uv run ruff check services
uv run mypy
uv run pytest
terraform -chdir=infrastructure/terraform fmt -check
terraform -chdir=infrastructure/terraform init -backend=false
terraform -chdir=infrastructure/terraform validate
