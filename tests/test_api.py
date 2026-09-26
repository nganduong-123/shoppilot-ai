from dataclasses import replace

from fastapi.testclient import TestClient

import app.channels.make as make_channel
import app.main as main_module
from app.config import settings
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


def test_make_bridge_requires_secret(monkeypatch):
    test_settings = replace(settings, make_bridge_secret="test-bridge-secret")
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(make_channel, "settings", test_settings)
    payload = {
        "event_id": "make-event-auth",
        "sender_id": "customer-1",
        "page_id": "page-1",
        "message": "Shop có áo màu đen không?",
    }
    with TestClient(app) as client:
        denied = client.post("/api/bridges/make/messenger/mint-fashion", json=payload)
        accepted = client.post(
            "/api/bridges/make/messenger/mint-fashion",
            json=payload,
            headers={"X-ShopPilot-Bridge-Key": "test-bridge-secret"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["replied"] is True
    assert accepted.json()["recipient_id"] == "customer-1"
    assert accepted.json()["reply"]


def test_make_bridge_is_idempotent(monkeypatch):
    test_settings = replace(settings, make_bridge_secret="test-bridge-secret")
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(make_channel, "settings", test_settings)
    payload = {
        "event_id": "make-event-duplicate",
        "sender_id": "customer-2",
        "page_id": "page-1",
        "message": "Còn hàng không?",
    }
    headers = {"X-ShopPilot-Bridge-Key": "test-bridge-secret"}
    with TestClient(app) as client:
        first = client.post(
            "/api/bridges/make/messenger/mint-fashion", json=payload, headers=headers
        )
        duplicate = client.post(
            "/api/bridges/make/messenger/mint-fashion", json=payload, headers=headers
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["replied"] is False
