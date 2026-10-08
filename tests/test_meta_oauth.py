from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app.meta_oauth as meta_oauth_module
import app.token_crypto as token_crypto_module
from app.config import settings
from app.main import app
from app.repository import repository


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeMetaClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url, params=None):
        if url.endswith("/oauth/access_token"):
            return FakeResponse({"access_token": "temporary-user-token"})
        if url.endswith("/me/accounts"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "page-oauth-1",
                            "name": "Page của chủ shop",
                            "access_token": "secret-page-token",
                            "tasks": ["MESSAGING", "MODERATE"],
                        }
                    ]
                }
            )
        raise AssertionError(url)

    async def post(self, url, params=None):
        assert url.endswith("/page-oauth-1/subscribed_apps")
        assert params["access_token"] == "secret-page-token"
        return FakeResponse({"success": True})


def test_owner_connects_page_without_sharing_facebook_password(monkeypatch):
    oauth_settings = replace(
        settings,
        meta_app_id="app-123",
        meta_app_secret="app-secret",
        public_base_url="https://shop.example",
        token_encryption_key="test-encryption-key",
    )
    monkeypatch.setattr(meta_oauth_module, "settings", oauth_settings)
    monkeypatch.setattr(token_crypto_module, "settings", oauth_settings)
    monkeypatch.setattr(meta_oauth_module.httpx, "AsyncClient", lambda **_: FakeMetaClient())

    registration = {
        "display_name": "Chủ Page",
        "email": "page-owner@example.com",
        "password": "mat-khau-shoppilot-123",
        "shop_name": "Shop OAuth",
        "shop_slug": "shop-oauth",
        "category": "Thời trang",
    }
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=registration).status_code == 201
        start = client.get("/api/shops/shop-oauth/integrations/meta/connect")
        assert start.status_code == 200
        authorization_url = start.json()["authorization_url"]
        query = parse_qs(urlparse(authorization_url).query)
        state = query["state"][0]

        callback = client.get(
            "/api/integrations/meta/callback",
            params={"state": state, "code": "authorization-code"},
            follow_redirects=False,
        )
        candidates = client.get(
            "/api/integrations/meta/candidates", params={"state": state}
        )
        complete = client.post(
            "/api/shops/shop-oauth/integrations/meta/complete",
            json={"state": state, "page_id": "page-oauth-1"},
        )

    assert query["client_id"] == ["app-123"]
    assert query["redirect_uri"] == [
        "https://shop.example/api/integrations/meta/callback"
    ]
    assert "pages_messaging" in query["scope"][0]
    assert callback.status_code == 303
    assert callback.headers["location"].startswith("/static/meta-connect.html?state=")
    assert candidates.status_code == 200
    assert candidates.json()["pages"] == [
        {
            "id": "page-oauth-1",
            "name": "Page của chủ shop",
            "tasks": ["MESSAGING", "MODERATE"],
        }
    ]
    assert "secret-page-token" not in candidates.text
    assert complete.status_code == 200

    connection = repository.get_channel_connection_by_external(
        "messenger", "page-oauth-1"
    )
    assert connection is not None
    assert connection["shop_id"] == complete.json()["shop_id"]
    assert connection["config"]["source"] == "meta_oauth"
    assert "secret-page-token" not in connection["config"]["page_access_token_enc"]
