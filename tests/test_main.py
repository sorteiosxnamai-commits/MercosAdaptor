from fastapi.testclient import TestClient

from app.client import MercosClient
from app.main import app
from app.security import require_api_key


def test_health_does_not_expose_credentials():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert "token" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_order_detail_route_requires_authentication():
    response = TestClient(app).get("/v1/orders/42")

    assert response.status_code in {401, 403}


def test_order_detail_route_preserves_v2_items(monkeypatch):
    async def detail(_self, resource: str, mercos_id: str):
        assert resource == "pedidos"
        assert mercos_id == "42"
        return {"id": 42, "itens": [{"id": 7, "produto_id": 3}]}

    monkeypatch.setattr(MercosClient, "get_detail", detail)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = TestClient(app).get("/v1/orders/42")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["itens"][0]["id"] == 7


def test_unsupported_detail_resource_returns_404():
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = TestClient(app).get("/v1/users/42")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
