from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.auth as auth_module
import app.main as main_module
from app.auth import hash_password, verify_password
from app.config import settings
from app.main import app


REGISTER_PAYLOAD = {
    "display_name": "Chủ Shop Test",
    "email": "owner@example.com",
    "password": "mat-khau-rieng-123",
    "shop_name": "Cửa hàng Test",
    "shop_slug": "cua-hang-test",
    "category": "Thời trang",
}


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("wrong password", first) is False


def test_register_login_session_and_logout():
    with TestClient(app) as client:
        registered = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        me = client.get("/api/auth/me")
        logged_out = client.post("/api/auth/logout")
        anonymous = client.get("/api/auth/me")
        denied = client.post(
            "/api/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-password"},
        )
        logged_in = client.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )

    assert registered.status_code == 201
    assert "password" not in registered.text
    assert registered.json()["shops"][0]["slug"] == "cua-hang-test"
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["email"] == "owner@example.com"
    assert logged_out.json() == {"logged_out": True}
    assert anonymous.json()["authenticated"] is False
    assert denied.status_code == 401
    assert logged_in.status_code == 200


def test_required_auth_scopes_dashboard_to_member_shops(monkeypatch):
    auth_settings = replace(settings, auth_required=True)
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)

    with TestClient(app) as anonymous_client:
        assert anonymous_client.get("/api/shops").status_code == 401

    with TestClient(app) as owner_client:
        registered = owner_client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        shops = owner_client.get("/api/shops")
        own_metrics = owner_client.get("/api/shops/cua-hang-test/metrics")
        other_metrics = owner_client.get("/api/shops/mint-fashion/metrics")

    assert registered.status_code == 201
    assert [shop["slug"] for shop in shops.json()] == ["cua-hang-test"]
    assert own_metrics.status_code == 200
    assert other_metrics.status_code == 403
