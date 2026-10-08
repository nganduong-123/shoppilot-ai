from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.repository import Repository, repository
from app.token_crypto import decrypt_secret, encrypt_secret


META_SCOPES = "pages_show_list,pages_manage_metadata,pages_messaging"


def state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


class MetaOAuthService:
    def __init__(self, repo: Repository = repository) -> None:
        self.repo = repo

    @property
    def configured(self) -> bool:
        return bool(settings.meta_app_id and settings.meta_app_secret)

    def callback_url(self) -> str:
        return f"{settings.public_base_url.rstrip('/')}/api/integrations/meta/callback"

    def start(self, user_id: str, shop_id: int) -> str:
        if not self.configured:
            raise RuntimeError("Meta App ID/App Secret chưa được cấu hình.")
        state = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
        self.repo.create_meta_oauth_state(state_digest(state), user_id, shop_id, expires_at)
        query = urlencode(
            {
                "client_id": settings.meta_app_id,
                "redirect_uri": self.callback_url(),
                "state": state,
                "scope": META_SCOPES,
                "response_type": "code",
            }
        )
        return f"https://www.facebook.com/{settings.meta_graph_api_version}/dialog/oauth?{query}"

    async def exchange_code(self, user_id: str, state: str, code: str) -> dict[str, Any]:
        record = self._state(user_id, state)
        async with httpx.AsyncClient(timeout=25) as client:
            token_response = await client.get(
                f"https://graph.facebook.com/{settings.meta_graph_api_version}/oauth/access_token",
                params={
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "redirect_uri": self.callback_url(),
                    "code": code,
                },
            )
            token_response.raise_for_status()
            user_token = token_response.json().get("access_token")
            if not user_token:
                raise RuntimeError("Meta không trả về access token.")
            pages_response = await client.get(
                f"https://graph.facebook.com/{settings.meta_graph_api_version}/me/accounts",
                params={
                    "access_token": user_token,
                    "fields": "id,name,access_token,tasks",
                    "limit": 100,
                },
            )
            pages_response.raise_for_status()
        candidates = []
        for page in pages_response.json().get("data", []):
            if page.get("id") and page.get("access_token"):
                candidates.append(
                    {
                        "id": str(page["id"]),
                        "name": str(page.get("name") or page["id"]),
                        "tasks": page.get("tasks", []),
                        "access_token_enc": encrypt_secret(str(page["access_token"])),
                    }
                )
        if not candidates:
            raise RuntimeError("Không tìm thấy Page nào mà tài khoản có quyền quản lý.")
        self.repo.set_meta_oauth_candidates(state_digest(state), candidates)
        return {"shop_id": record["shop_id"], "pages": self.public_candidates(candidates)}

    def candidates(self, user_id: str, state: str) -> dict[str, Any]:
        record = self._state(user_id, state)
        return {
            "shop_id": record["shop_id"],
            "pages": self.public_candidates(record["candidates"]),
        }

    async def complete(
        self, user_id: str, shop_id: int, state: str, page_id: str
    ) -> dict[str, Any]:
        record = self._state(user_id, state)
        if record["shop_id"] != shop_id:
            raise ValueError("Phiên kết nối không thuộc cửa hàng này.")
        page = next((item for item in record["candidates"] if item["id"] == page_id), None)
        if not page:
            raise ValueError("Page không có trong danh sách đã cấp quyền.")
        existing = self.repo.get_channel_connection_by_external("messenger", page_id)
        if existing and existing["shop_id"] != shop_id:
            raise ValueError("Page này đã được kết nối với một workspace ShopPilot khác.")
        page_token = decrypt_secret(page["access_token_enc"])
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"https://graph.facebook.com/{settings.meta_graph_api_version}/{page_id}/subscribed_apps",
                params={
                    "access_token": page_token,
                    "subscribed_fields": "messages,messaging_postbacks",
                },
            )
            response.raise_for_status()
        connection = self.repo.upsert_channel_connection(
            shop_id,
            "messenger",
            page_id,
            page["name"],
            {
                "source": "meta_oauth",
                "page_access_token_enc": page["access_token_enc"],
                "tasks": page.get("tasks", []),
            },
        )
        self.repo.consume_meta_oauth_state(state_digest(state))
        return connection

    def _state(self, user_id: str, state: str) -> dict[str, Any]:
        record = self.repo.get_meta_oauth_state(
            state_digest(state), user_id, datetime.now(UTC).isoformat()
        )
        if not record:
            raise ValueError("Phiên kết nối Meta đã hết hạn hoặc không hợp lệ.")
        return record

    @staticmethod
    def public_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"id": page["id"], "name": page["name"], "tasks": page.get("tasks", [])}
            for page in candidates
        ]


meta_oauth_service = MetaOAuthService()
