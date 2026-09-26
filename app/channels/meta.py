from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import httpx

from app.channels.base import ChannelAdapter, InboundMessage
from app.config import Settings, settings


class MetaMessengerAdapter(ChannelAdapter):
    channel = "messenger"

    def __init__(
        self,
        config: Settings | None = None,
        *,
        page_id: str | None = None,
        page_access_token: str | None = None,
    ) -> None:
        self.config = config or settings
        self.page_id = page_id or self.config.meta_page_id
        self.page_access_token = page_access_token or self.config.meta_page_access_token

    @property
    def configured(self) -> bool:
        return bool(
            self.config.meta_app_secret
            and self.page_access_token
            and self.config.meta_verify_token
            and self.page_id
        )

    def verify_signature(self, body: bytes, signature: str | None) -> bool:
        if not signature or not self.config.meta_app_secret:
            return False
        expected = "sha256=" + hmac.new(
            self.config.meta_app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_challenge(self, mode: str | None, token: str | None) -> bool:
        return bool(
            mode == "subscribe"
            and token
            and self.config.meta_verify_token
            and hmac.compare_digest(token, self.config.meta_verify_token)
        )

    def parse_signed_request(self, signed_request: str) -> dict[str, Any]:
        if not self.config.meta_app_secret:
            raise ValueError("META_APP_SECRET chưa được cấu hình.")
        try:
            encoded_signature, encoded_payload = signed_request.split(".", 1)
            signature = self._decode_base64url(encoded_signature)
            payload = json.loads(self._decode_base64url(encoded_payload))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Meta signed_request không hợp lệ.") from exc

        algorithm = str(payload.get("algorithm", "HMAC-SHA256")).upper()
        if algorithm != "HMAC-SHA256":
            raise ValueError("Thuật toán signed_request không được hỗ trợ.")
        expected = hmac.new(
            self.config.meta_app_secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Chữ ký Meta signed_request không hợp lệ.")
        if not payload.get("user_id"):
            raise ValueError("Meta signed_request thiếu user_id.")
        return payload

    @staticmethod
    def _decode_base64url(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def parse_events(self, payload: dict[str, Any]) -> list[InboundMessage]:
        if payload.get("object") != "page":
            return []
        events: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            page_id = str(entry.get("id") or self.page_id or "")
            for item in entry.get("messaging", []):
                message = item.get("message") or {}
                sender_id = str((item.get("sender") or {}).get("id") or "")
                mid = str(message.get("mid") or "")
                text = message.get("text")
                if not sender_id or not mid or not text or message.get("is_echo"):
                    continue
                events.append(
                    InboundMessage(
                        channel=self.channel,
                        external_event_id=mid,
                        external_conversation_id=f"{page_id}:{sender_id}",
                        external_customer_id=sender_id,
                        text=str(text).strip(),
                        metadata={
                            "page_id": page_id,
                            "timestamp": item.get("timestamp"),
                            "referral": item.get("referral"),
                            "attachments": message.get("attachments", []),
                        },
                    )
                )
        return events

    async def send_text(self, recipient_id: str, text: str) -> dict[str, Any]:
        if not self.page_access_token:
            raise RuntimeError("META_PAGE_ACCESS_TOKEN chưa được cấu hình.")
        url = (
            f"https://graph.facebook.com/{self.config.meta_graph_api_version}"
            "/me/messages"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                params={"access_token": self.page_access_token},
                json={
                    "recipient": {"id": recipient_id},
                    "messaging_type": "RESPONSE",
                    "message": {"text": text},
                },
            )
            response.raise_for_status()
            return response.json()
