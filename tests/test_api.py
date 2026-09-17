from fastapi.testclient import TestClient

from app.main import app


def test_health_and_catalog_endpoints():
    with TestClient(app) as client:
        health = client.get("/api/health")
        shops = client.get("/api/shops")
        products = client.get("/api/shops/mint-fashion/products")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert len(shops.json()) == 3
    assert len(products.json()) == 5


def test_unknown_shop_returns_404():
    with TestClient(app) as client:
        response = client.get("/api/shops/not-a-real-shop/products")

    assert response.status_code == 404
