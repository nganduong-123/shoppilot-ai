from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_module
import app.main as main_module
from app.auth import hash_password, verify_password
from app.config import settings
from app.main import app
from app.repository import AccountDeletionBlocked, repository

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


def test_optional_auth_still_scopes_an_authenticated_dashboard(monkeypatch):
    auth_settings = replace(settings, auth_required=False)
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)

    with TestClient(app) as owner_client:
        owner_client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        shops = owner_client.get("/api/shops")
        other_metrics = owner_client.get("/api/shops/mint-fashion/metrics")

    assert [shop["slug"] for shop in shops.json()] == ["cua-hang-test"]
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
        confirmed = client.post("/api/auth/verify-email/confirm", json={"token": sent["token"]})
        reused = client.post("/api/auth/verify-email/confirm", json={"token": sent["token"]})
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


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/password-reset/request",
        "/api/auth/verify-email/request",
    ],
)
def test_account_email_unavailable_is_not_reported_as_sent(monkeypatch, path):
    monkeypatch.setattr(main_module, "email_service", SimpleNamespace(configured=False))
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=REGISTER_PAYLOAD).status_code == 201
        capability = client.get("/api/auth/me").json()
        known = client.post(path, json={"email": REGISTER_PAYLOAD["email"]})
        unknown = client.post(path, json={"email": "missing@example.com"})
    assert capability["password_reset_available"] is False
    assert known.status_code == unknown.status_code == 503
    assert known.json() == unknown.json()
    assert "accepted" not in known.json()


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_password_reset_provider_failure_keeps_account_existence_private(
    monkeypatch, caplog, failure
):
    def failed_delivery(*args):
        if failure == "exception":
            raise httpx.ConnectError("private provider diagnostics must not be returned")
        return False

    fake_email = SimpleNamespace(configured=True, send_password_reset=failed_delivery)
    monkeypatch.setattr(main_module, "email_service", fake_email)
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=REGISTER_PAYLOAD).status_code == 201
        response = client.post(
            "/api/auth/password-reset/request", json={"email": REGISTER_PAYLOAD["email"]}
        )
        unknown = client.post(
            "/api/auth/password-reset/request", json={"email": "missing@example.com"}
        )
    assert response.status_code == unknown.status_code == 202
    assert response.json() == unknown.json() == {"accepted": True}
    assert "private provider" not in response.text
    assert "Account email delivery failed" in caplog.text
    assert "private provider" not in caplog.text


def test_registration_survives_verification_delivery_failure_and_can_resend(monkeypatch):
    auth_settings = replace(settings, email_verification_required=True)
    sent = {}
    fake_email = SimpleNamespace(configured=True, send_verification=lambda *args: False)
    monkeypatch.setattr(main_module, "settings", auth_settings)
    monkeypatch.setattr(auth_module, "settings", auth_settings)
    monkeypatch.setattr(main_module, "email_service", fake_email)
    with TestClient(app) as client:
        registered = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert registered.status_code == 201
        assert registered.json()["verification_email_sent"] is False
        fake_email.send_verification = lambda email, name, token: sent.update(token=token) or True
        response = client.post(
            "/api/auth/verify-email/request", json={"email": REGISTER_PAYLOAD["email"]}
        )
        confirmed = client.post("/api/auth/verify-email/confirm", json={"token": sent["token"]})
    assert response.status_code == 202
    assert confirmed.json() == {"verified": True}


def test_registration_non_unique_integrity_errors_are_not_misreported(monkeypatch):
    import sqlite3

    def fail(*args, **kwargs):
        raise sqlite3.IntegrityError("NOT NULL constraint failed: shops.category")

    monkeypatch.setattr(main_module.auth_service, "register", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/auth/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 500


def test_operator_recovery_checks_ownership_and_uses_single_use_link():
    from scripts.recover_account import issue_recovery_link

    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        with pytest.raises(ValueError):
            issue_recovery_link(REGISTER_PAYLOAD["email"], "another-shop", "https://example.com")
        with pytest.raises(ValueError):
            issue_recovery_link(REGISTER_PAYLOAD["email"], "cua-hang-test", "http://example.com")
        link, user_id = issue_recovery_link(
            REGISTER_PAYLOAD["email"], "cua-hang-test", "https://example.com"
        )
        token = link.split("?reset=", 1)[1]
        payload = {"token": token, "password": "recovered-password-123"}
        reset = client.post("/api/auth/password-reset/confirm", json=payload)
        reused = client.post("/api/auth/password-reset/confirm", json=payload)
        old_session = client.get("/api/auth/me")
    assert user_id
    assert reset.status_code == 200
    assert reused.status_code == 400
    assert old_session.json()["authenticated"] is False


def test_authenticated_user_can_change_password_and_keep_only_new_session():
    other_session = TestClient(app)
    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert (
            other_session.post(
                "/api/auth/login",
                json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
            ).status_code
            == 200
        )
        wrong = client.post(
            "/api/auth/password/change",
            json={"current_password": "wrong", "new_password": "new-password-456"},
        )
        changed = client.post(
            "/api/auth/password/change",
            json={
                "current_password": REGISTER_PAYLOAD["password"],
                "new_password": "new-password-456",
            },
        )
        assert wrong.status_code == 401
        assert changed.json() == {"changed": True}
        assert client.get("/api/auth/me").json()["authenticated"] is True
        assert other_session.get("/api/auth/me").json()["authenticated"] is False
        assert (
            client.post(
                "/api/auth/password/change",
                json={"current_password": "new-password-456", "new_password": "new-password-456"},
            ).status_code
            == 400
        )
    other_session.close()


def test_account_page_requires_login():
    with TestClient(app) as client:
        assert client.get("/account").status_code == 401
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        assert client.get("/account").status_code == 200


def test_user_can_inspect_and_revoke_other_sessions_without_exposing_tokens():
    other = TestClient(app)
    with TestClient(app) as client:
        client.post(
            "/api/auth/register",
            json=REGISTER_PAYLOAD,
            headers={"User-Agent": "Primary Browser Windows"},
        )
        other.post(
            "/api/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
            headers={"User-Agent": "Other Browser Android"},
        )
        sessions = client.get("/api/auth/sessions")
        assert sessions.status_code == 200
        assert len(sessions.json()["sessions"]) == 2
        assert sum(item["current"] for item in sessions.json()["sessions"]) == 1
        assert "token" not in sessions.text.casefold()
        assert {item["user_agent"] for item in sessions.json()["sessions"]} == {
            "Primary Browser Windows",
            "Other Browser Android",
        }
        current = next(item for item in sessions.json()["sessions"] if item["current"])
        assert client.delete(f"/api/auth/sessions/{current['id']}").status_code == 400
        target = next(item for item in sessions.json()["sessions"] if not item["current"])
        assert other.delete(f"/api/auth/sessions/{current['id']}").status_code == 200
        assert client.get("/api/auth/me").json()["authenticated"] is False
        client.post(
            "/api/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert client.delete("/api/auth/sessions/missing-session").status_code == 404
        assert client.delete(f"/api/auth/sessions/{target['id']}").status_code == 200
        assert other.get("/api/auth/me").json()["authenticated"] is False
        other.post(
            "/api/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        revoked = client.post("/api/auth/sessions/revoke-others")
        assert revoked.json() == {"revoked": 1}
        assert client.get("/api/auth/me").json()["authenticated"] is True
        assert other.get("/api/auth/me").json()["authenticated"] is False
    other.close()


def test_account_export_is_scoped_and_contains_no_credentials():
    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        exported = client.get("/api/auth/account/export")
    assert exported.status_code == 200
    assert exported.json()["account"]["email"] == REGISTER_PAYLOAD["email"]
    assert exported.json()["memberships"] == [
        {
            "shop_slug": REGISTER_PAYLOAD["shop_slug"],
            "shop_name": REGISTER_PAYLOAD["shop_name"],
            "role": "owner",
            "joined_workspace_created_at": exported.json()["memberships"][0][
                "joined_workspace_created_at"
            ],
        }
    ]
    assert "password" not in exported.text.casefold()
    assert "token" not in exported.text.casefold()


def test_account_deletion_requires_password_and_another_shop_owner():
    replacement_payload = {
        **REGISTER_PAYLOAD,
        "email": "replacement@example.com",
        "display_name": "Replacement Owner",
        "shop_name": "Replacement Shop",
        "shop_slug": "replacement-shop",
    }
    replacement_client = TestClient(app)
    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        replacement_client.post("/api/auth/register", json=replacement_payload)
        wrong = client.request(
            "DELETE",
            "/api/auth/account",
            json={"password": "wrong", "confirmation": "DELETE"},
        )
        blocked = client.request(
            "DELETE",
            "/api/auth/account",
            json={"password": REGISTER_PAYLOAD["password"], "confirmation": "DELETE"},
        )
        assert wrong.status_code == 401
        assert blocked.status_code == 409
        assert REGISTER_PAYLOAD["shop_slug"] in blocked.json()["detail"]
        shop = repository.get_shop(REGISTER_PAYLOAD["shop_slug"])
        replacement = repository.get_user_by_email(replacement_payload["email"])
        repository.add_shop_member(shop["id"], replacement["id"], "owner")
        deleted = client.request(
            "DELETE",
            "/api/auth/account",
            json={"password": REGISTER_PAYLOAD["password"], "confirmation": "DELETE"},
        )
        assert deleted.json() == {"deleted": True}
        assert client.get("/api/auth/me").json()["authenticated"] is False
        assert repository.get_user_by_email(REGISTER_PAYLOAD["email"]) is None
        assert repository.get_shop_member(shop["id"], replacement["id"])["role"] == "owner"
    replacement_client.close()


def test_platform_admin_account_deletion_is_blocked():
    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        repository.grant_platform_admin(
            REGISTER_PAYLOAD["email"], "Protected operator test"
        )
        deleted = client.request(
            "DELETE",
            "/api/auth/account",
            json={
                "password": REGISTER_PAYLOAD["password"],
                "confirmation": "DELETE",
            },
        )
        assert deleted.status_code == 409
        assert "quản trị nền tảng" in deleted.json()["detail"]
        assert client.get("/api/auth/me").json()["authenticated"] is True


def test_concurrent_owner_deletions_preserve_workspace_ownership():
    with TestClient(app) as client:
        client.post("/api/auth/register", json=REGISTER_PAYLOAD)
        first = repository.get_user_by_email(REGISTER_PAYLOAD["email"])
        second, _ = auth_module.auth_service.register(
            {**REGISTER_PAYLOAD, "email": "second@example.com", "shop_slug": "second-shop"}
        )
        first_shop = repository.get_shop(REGISTER_PAYLOAD["shop_slug"])
        second_shop = repository.get_shop("second-shop")
        repository.add_shop_member(first_shop["id"], second["id"], "owner")
        repository.add_shop_member(second_shop["id"], first["id"], "owner")
        barrier = Barrier(2)

        def delete(user_id):
            barrier.wait(timeout=10)
            try:
                return repository.delete_user_account(user_id)
            except AccountDeletionBlocked:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(delete, [first["id"], second["id"]]))
        assert sorted(results) == [False, True]
        for shop in [first_shop, second_shop]:
            members = repository.list_shop_team(shop["id"])["members"]
            assert len(members) == 1
            assert members[0]["role"] == "owner"
