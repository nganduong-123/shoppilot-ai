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


def test_meta_review_pages_and_public_urls_are_available():
    with TestClient(app) as client:
        privacy = client.get("/privacy")
        terms = client.get("/terms")
        deletion = client.get("/data-deletion")
        status = client.get("/api/integrations/meta/status")

    assert privacy.status_code == 200
    assert "Chính sách quyền riêng tư" in privacy.text
    assert terms.status_code == 200
    assert "Điều khoản sử dụng" in terms.text
    assert deletion.status_code == 200
    assert "Yêu cầu xóa dữ liệu" in deletion.text
    assert status.status_code == 200
    assert status.json()["urls"]["data_deletion_callback"].endswith(
        "/api/meta/data-deletion"
    )
