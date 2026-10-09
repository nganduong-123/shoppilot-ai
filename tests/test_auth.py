from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

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


def test_owner_can_update_shop_profile_but_not_another_tenant(monkeypatch):
    auth_settings = replace(settings, auth_required=True)
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)

    with TestClient(app) as owner_client:
        registered = owner_client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        updated = owner_client.patch(
            "/api/shops/cua-hang-test",
            json={
                "tagline": "Thời trang thiết thực cho mỗi ngày",
                "policy_text": "Đổi sản phẩm nguyên tem trong bảy ngày kể từ ngày nhận hàng.",
                "voice": "Thân thiện, rõ ràng và không suy đoán thông tin.",
            },
        )
        forbidden = owner_client.patch(
            "/api/shops/mint-fashion",
            json={"tagline": "Không được phép sửa dữ liệu của tenant khác"},
        )

    assert registered.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["tagline"] == "Thời trang thiết thực cho mỗi ngày"
    assert "bảy ngày" in updated.json()["policy_text"]
    assert forbidden.status_code == 403


def test_owner_can_claim_an_unowned_seeded_demo_shop():
    payload = {
        **REGISTER_PAYLOAD,
        "email": "mint-owner@example.com",
        "shop_name": "MisterBox Men",
        "shop_slug": "mint-fashion",
    }

    with TestClient(app) as owner_client:
        registered = owner_client.post("/api/auth/register", json=payload)
        own_metrics = owner_client.get("/api/shops/mint-fashion/metrics")

    with TestClient(app) as second_client:
        duplicate = second_client.post(
            "/api/auth/register",
            json={**payload, "email": "second-owner@example.com"},
        )

    assert registered.status_code == 201
    assert registered.json()["shops"][0]["slug"] == "mint-fashion"
    assert registered.json()["shops"][0]["product_count"] > 0
    assert own_metrics.status_code == 200
    assert duplicate.status_code == 409


def test_required_email_verification_blocks_login_until_confirmed(monkeypatch):
    auth_settings = replace(settings, email_verification_required=True)
    sent: dict[str, str] = {}
    fake_email = SimpleNamespace(
        configured=True,
        send_verification=lambda email, name, token: sent.update(token=token) or True,
        send_password_reset=lambda email, name, token: True,
    )
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)
    monkeypatch.setattr(main_module, "email_service", fake_email)

    with TestClient(app) as client:
        registered = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        me_before = client.get("/api/auth/me")
        blocked = client.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )
        confirmed = client.post(
            "/api/auth/verify-email/confirm", json={"token": sent["token"]}
        )
        reused = client.post(
            "/api/auth/verify-email/confirm", json={"token": sent["token"]}
        )
        logged_in = client.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )

    assert registered.status_code == 201
    assert registered.json()["verification_required"] is True
    assert me_before.json()["authenticated"] is False
    assert blocked.status_code == 403
    assert confirmed.json() == {"verified": True}
    assert reused.status_code == 400
    assert logged_in.status_code == 200


def test_restart_does_not_auto_verify_a_new_unverified_account(monkeypatch):
    auth_settings = replace(settings, email_verification_required=True)
    fake_email = SimpleNamespace(
        configured=True,
        send_verification=lambda email, name, token: True,
        send_password_reset=lambda email, name, token: True,
    )
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)
    monkeypatch.setattr(main_module, "email_service", fake_email)

    with TestClient(app) as client:
        registered = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert registered.status_code == 201
        from app.database import init_database
        init_database()
        blocked = client.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )

    assert blocked.status_code == 403


def test_password_reset_revokes_sessions_and_changes_password(monkeypatch):
    sent: dict[str, str] = {}
    fake_email = SimpleNamespace(
        configured=True,
        send_verification=lambda email, name, token: True,
        send_password_reset=lambda email, name, token: sent.update(token=token) or True,
    )
    monkeypatch.setattr(main_module, "email_service", fake_email)

    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=REGISTER_PAYLOAD).status_code == 201
        requested = client.post(
            "/api/auth/password-reset/request",
            json={"email": REGISTER_PAYLOAD["email"]},
        )
        reset = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": sent["token"], "password": "mat-khau-moi-456"},
        )
        session_after_reset = client.get("/api/auth/me")
        old_login = client.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )
        new_login = client.post(
            "/api/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "mat-khau-moi-456"},
        )

    assert requested.status_code == 202
    assert requested.json() == {"accepted": True}
    assert reset.json() == {"reset": True}
    assert session_after_reset.json()["authenticated"] is False
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_password_reset_request_does_not_reveal_unknown_email(monkeypatch):
    fake_email = SimpleNamespace(
        configured=True,
        send_verification=lambda email, name, token: True,
        send_password_reset=lambda email, name, token: True,
    )
    monkeypatch.setattr(main_module, "email_service", fake_email)

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/password-reset/request",
            json={"email": "missing@example.com"},
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
