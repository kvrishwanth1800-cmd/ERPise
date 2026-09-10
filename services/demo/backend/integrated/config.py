"""Runtime configuration for the governed demo entry point."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoIdentity:
    tenant_id: str = "demo-tenant"
    user_id: str = "demo-admin"
    role: str = "administrator"
    organization_id: str = "demo-store"


IDENTITY = DemoIdentity()
