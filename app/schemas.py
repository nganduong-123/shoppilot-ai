from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    status: str
    quick_replies: list[str] = []
    products: list[dict[str, Any]] = []
    draft_order: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    model: str


class ShopCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]+$", min_length=3, max_length=50)
    name: str = Field(min_length=2, max_length=100)
    category: str = Field(min_length=2, max_length=60)
    tagline: str = Field(min_length=2, max_length=160)
    policy_text: str = Field(min_length=20, max_length=8000)
    voice: str = Field(default="Thân thiện, ngắn gọn và trung thực.", max_length=500)


class ProductCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=160)
    category: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=1000)
    price: int = Field(ge=0)
    stock: int = Field(ge=0)
    attributes: dict[str, Any] = {}


class WebChannelMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    customer_id: str | None = Field(default=None, max_length=120)
    customer_name: str | None = Field(default=None, max_length=120)


class MakeMessengerMessage(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    sender_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    page_id: str = Field(default="make-page", min_length=1, max_length=200)
    customer_name: str | None = Field(default=None, max_length=120)


class BotControlRequest(BaseModel):
    enabled: bool
    assigned_to: str | None = Field(default=None, max_length=120)


class HumanReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    agent_name: str = Field(default="Dương Thị Ngân", min_length=2, max_length=120)
    suggestion_id: int | None = Field(default=None, ge=1)


class InboxActionRequest(BaseModel):
    action: Literal["takeover", "resolve", "reopen"]
    agent_name: str = Field(default="Dương Thị Ngân", min_length=2, max_length=120)
