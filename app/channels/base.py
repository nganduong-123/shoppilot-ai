from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InboundMessage:
    channel: str
    external_event_id: str
    external_conversation_id: str
    external_customer_id: str
    text: str
    customer_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    channel: str

    @abstractmethod
    async def send_text(self, recipient_id: str, text: str) -> dict[str, Any]:
        """Deliver an outbound reply and return provider metadata."""
