from __future__ import annotations

from typing import Any

import httpx

from app.channels.base import ChannelAdapter
from app.config import Settings, settings


class MakeMessengerAdapter(ChannelAdapter):
    """Bridge Messenger through a Make scenario instead of a private Meta app."""

    channel = "messenger"

    def __init__(
        self,
        config: Settings | None = None,
        *,
        response_only: bool = False,
    ) -> None:
        self.config = config or settings
        self.response_only = response_only

    @property
    def inbound_configured(self) -> bool:
        return bool(self.config.make_bridge_secret)

    @property
    def outbound_configured(self) -> bool:
        return bool(self.config.make_messenger_outbound_webhook_url)

    async def send_text(self, recipient_id: str, text: str) -> dict[str, Any]:
        # The inbound scenario reads the reply from ShopPilot's HTTP response
        # and maps it into Make's Messenger "Send a Message" module.
        if self.response_only:
            return {
                "provider": "make",
                "mode": "scenario-response",
                "recipient_id": recipient_id,
            }

        if not self.config.make_messenger_outbound_webhook_url:
            raise RuntimeError("MAKE_MESSENGER_OUTBOUND_WEBHOOK_URL chưa được cấu hình.")

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.config.make_messenger_outbound_webhook_url,
                json={"recipient_id": recipient_id, "message": text},
            )
            response.raise_for_status()
            if response.headers.get("content-type", "").startswith("application/json"):
                result = response.json()
            else:
                result = {"response": response.text[:500]}
        return {"provider": "make", "mode": "outbound-webhook", **result}
