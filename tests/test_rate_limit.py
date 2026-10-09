from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.main import app
from app.rate_limit import PersistentRateLimiter


def test_sliding_window_allows_requests_again_after_expiry():
    limiter = PersistentRateLimiter()

    assert limiter.check("auth", "client-a", 2, 10, now=101) is None
    assert limiter.check("auth", "client-a", 2, 10, now=102) is None
    assert limiter.check("auth", "client-a", 2, 10, now=103) == 7
    assert limiter.check("auth", "client-b", 2, 10, now=103) is None
    assert limiter.check("auth", "client-a", 2, 10, now=111) is None


def test_limiter_state_is_shared_between_instances():
    first = PersistentRateLimiter()
    second = PersistentRateLimiter()

    assert first.check("auth", "client-a", 1, 60, now=100) is None
    assert second.check("auth", "client-a", 1, 60, now=101) == 19


def test_login_rate_limit_returns_retry_after(monkeypatch):
    limited_settings = replace(
        settings,
        rate_limit_enabled=True,
        auth_rate_limit_requests=2,
        auth_rate_limit_window_seconds=300,
    )
    monkeypatch.setattr(main_module, "settings", limited_settings)

    payload = {"email": "unknown@example.com", "password": "wrong-password"}
    with TestClient(app) as client:
        first = client.post("/api/auth/login", json=payload)
        second = client.post("/api/auth/login", json=payload)
        blocked = client.post("/api/auth/login", json=payload)

    assert first.status_code == 401
    assert second.status_code == 401
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_rate_limit_can_be_disabled_for_local_development(monkeypatch):
    unlimited_settings = replace(
        settings,
        rate_limit_enabled=False,
        auth_rate_limit_requests=1,
    )
    monkeypatch.setattr(main_module, "settings", unlimited_settings)

    payload = {"email": "unknown@example.com", "password": "wrong-password"}
    with TestClient(app) as client:
        responses = [client.post("/api/auth/login", json=payload) for _ in range(3)]

    assert [response.status_code for response in responses] == [401, 401, 401]


def test_public_chat_uses_a_separate_rate_limit(monkeypatch):
    limited_settings = replace(
        settings,
        rate_limit_enabled=True,
        chat_rate_limit_requests=1,
        chat_rate_limit_window_seconds=60,
    )
    monkeypatch.setattr(main_module, "settings", limited_settings)

    payload = {"message": "Shop có áo sơ mi nào?"}
    with TestClient(app) as client:
        first = client.post("/api/shops/mint-fashion/chat", json=payload)
        blocked = client.post("/api/shops/mint-fashion/chat", json=payload)

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Bạn gửi yêu cầu quá nhanh. Vui lòng thử lại sau."
