from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import settings


TEST_ADMIN_PASSWORD = "Admin-backend-tests-123"


def bootstrap_admin_response(
    client: TestClient,
    *,
    password: str = TEST_ADMIN_PASSWORD,
) -> Response:
    """Initialize the bootstrap administrator once, then log in on reuse."""
    response = client.post(
        "/auth/setup",
        json={
            "new_password": password,
            "password_confirmation": password,
        },
    )
    if response.status_code == 409:
        response = client.post(
            "/auth/login",
            json={
                "username": settings.bootstrap_username,
                "password": password,
            },
        )
    assert response.status_code == 200, response.text
    return response


def bootstrap_admin(
    client: TestClient,
    *,
    password: str = TEST_ADMIN_PASSWORD,
) -> dict[str, Any]:
    return bootstrap_admin_response(client, password=password).json()


def bootstrap_admin_headers(client: TestClient) -> dict[str, str]:
    payload = bootstrap_admin(client)
    return {"Authorization": f"Bearer {payload['access_token']}"}
