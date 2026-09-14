# ruff: noqa: E501
"""Authenticated backend client for the internal edge reservation service."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

EDGE_URL = os.environ.get("EDGE_URL", "http://edge:8090")
EDGE_SERVICE_KEY = os.environ.get("EDGE_SERVICE_KEY", "demo-edge-service-key")


def request(path: str, tenant_id: str, payload: dict[str, object] | None = None, idempotency_key: str = "") -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"X-Tenant-Id": tenant_id, "X-Edge-Service-Key": EDGE_SERVICE_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    method = "GET" if data is None else "POST"
    try:
        with urlopen(Request(f"{EDGE_URL}{path}", data=data, headers=headers, method=method), timeout=3) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        return json.loads(error.read().decode())


def status(tenant_id: str) -> dict[str, Any]:
    return request("/", tenant_id)


def reserve(tenant_id: str, reservation_id: str, quantity: int, key: str) -> dict[str, Any]:
    return request("/reserve", tenant_id, {"reservation_id": reservation_id, "quantity": quantity}, key)


def transition(tenant_id: str, action: str, reservation_id: str) -> dict[str, Any]:
    return request(f"/{action}", tenant_id, {"reservation_id": reservation_id})
