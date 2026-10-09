from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.config import Settings, settings


class CommerceConnector:
    """Signed HTTP bridge for shipping providers and commerce/order systems."""

    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def quote_shipping(
        self,
        *,
        shop_slug: str,
        location: str,
        order_value: int,
        currency: str,
    ) -> dict[str, Any] | None:
        if not self.config.shipping_quote_url:
            return None
        payload = {
            "shop_slug": shop_slug,
            "location": location,
            "order_value": order_value,
            "currency": currency,
        }
        response = httpx.post(
            self.config.shipping_quote_url,
            content=self._body(payload),
            headers=self._headers(payload),
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        fee = int(result["fee"])
        if fee < 0:
            raise ValueError("Shipping provider returned a negative fee")
        return {
            "location": location,
            "fee": fee,
            "eta": str(result.get("eta") or "Theo đơn vị vận chuyển"),
            "provider": str(result.get("provider") or "connected-provider"),
            "source": "shipping_provider",
        }

    async def send_confirmed_order(self, order: dict[str, Any]) -> dict[str, Any]:
        if not self.config.commerce_order_webhook_url:
            raise RuntimeError("COMMERCE_ORDER_WEBHOOK_URL chưa được cấu hình.")
        body = self._body(order)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.config.commerce_order_webhook_url,
                content=body,
                headers=self._headers(order),
            )
        response.raise_for_status()
        if not response.content:
            return {"accepted": True}
        return response.json()

    @staticmethod
    def _body(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def _headers(self, payload: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.integration_webhook_secret:
            signature = hmac.new(
                self.config.integration_webhook_secret.encode("utf-8"),
                self._body(payload),
                hashlib.sha256,
            ).hexdigest()
            headers["X-ShopPilot-Signature"] = f"sha256={signature}"
        return headers


commerce_connector = CommerceConnector()
