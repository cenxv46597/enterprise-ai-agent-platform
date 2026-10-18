"""Public HTTP contract for the process health endpoint."""

from fastapi.testclient import TestClient
import pytest

from app.api import health
from app.main import app


def test_health_returns_process_status() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("available,expected", [(True, 200), (False, 503)])
def test_ready_is_registered_in_the_actual_application(monkeypatch, available, expected):
    monkeypatch.setattr(health, "database_is_ready", lambda: available)
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
