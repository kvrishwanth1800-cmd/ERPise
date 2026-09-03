#!/usr/bin/env sh
set -eu

corepack enable
pnpm install --lockfile=false
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
