# Workspace quality gates

Run `sh scripts/verify.sh` from the repository root. It validates the TypeScript, Rust, Python, and Terraform workspaces in this order:

1. pnpm format, lint, type checking, and tests.
2. Cargo formatting, Clippy with warnings denied, and tests.
3. uv-managed Ruff, mypy strict mode, and pytest.
4. Terraform formatting, offline initialization, and validation.

The first run may create language lockfiles because this foundation begins with no dependency lock artifacts. Commit generated lockfiles with the work order that introduces them. Package and tool versions remain exact in the workspace manifests.

The command does not start infrastructure and does not use production credentials. Use `make local-up` separately when a work order requires local dependencies.
