from __future__ import annotations

from typing import Any

from app.channels.base import ChannelAdapter


class WebChannelAdapter(ChannelAdapter):
    channel = "web"

    async def send_text(self, recipient_id: str, text: str) -> dict[str, Any]:
        # The HTTP endpoint returns the reply directly to the browser widget.
        return {"delivered": True, "transport": "http-response"}
