# WO-2 implementation plan

Create a small polyglot workspace baseline. Each language has one tested primitive, and a single verifier runs TypeScript, Rust, Python, and Terraform quality gates.

## Files
- TypeScript manifests, lint, formatting, strict compiler settings, contract test
- Cargo workspace and tested transaction primitive
- uv project, strict static analysis, typed health primitive test
- Terraform version constraint and validation configuration
- CI quality workflow and local runbook

## Rollback
Revert the focused workspace commit. No schema, production environment, or external credential changes occur.
