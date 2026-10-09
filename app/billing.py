from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings, settings
from app.repository import Repository, repository

PLANS = {
    "free": {"name": "Free", "monthly_messages": 500, "seats": 1},
    "pro": {"name": "Pro", "monthly_messages": 10000, "seats": 5},
    "business": {"name": "Business", "monthly_messages": 50000, "seats": 20},
}


class StripeBillingService:
    def __init__(
        self,
        config: Settings = settings,
        repo: Repository = repository,
    ) -> None:
        self.config = config
        self.repo = repo

    @property
    def configured(self) -> bool:
        return bool(
            self.config.stripe_secret_key
            and self.config.stripe_webhook_secret
            and self.config.stripe_price_pro
            and self.config.stripe_price_business
        )

    def price_id(self, plan: str) -> str | None:
        if plan == "pro":
            return self.config.stripe_price_pro
        if plan == "business":
            return self.config.stripe_price_business
        return None

    def create_checkout(self, *, shop: dict[str, Any], user: dict[str, Any], plan: str) -> str:
        price_id = self.price_id(plan)
        if not self.config.stripe_secret_key or not price_id:
            raise RuntimeError("Gói thanh toán này chưa được cấu hình trên Stripe.")
        base_url = self.config.public_base_url.rstrip("/")
        response = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(self.config.stripe_secret_key, ""),
            headers={"Idempotency-Key": f"checkout-{shop['id']}-{plan}-{uuid4()}"},
            data={
                "mode": "subscription",
                "customer_email": user["email"],
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{base_url}/?view=billing&checkout=success",
                "cancel_url": f"{base_url}/?view=billing&checkout=cancelled",
                "metadata[shop_id]": str(shop["id"]),
                "metadata[plan]": plan,
                "subscription_data[metadata][shop_id]": str(shop["id"]),
                "subscription_data[metadata][plan]": plan,
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["url"]

    def create_portal(self, customer_id: str) -> str:
        if not self.config.stripe_secret_key:
            raise RuntimeError("Stripe chưa được cấu hình.")
        response = httpx.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(self.config.stripe_secret_key, ""),
            data={
                "customer": customer_id,
                "return_url": f"{self.config.public_base_url.rstrip('/')}/?view=billing",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["url"]

    def verify_signature(
        self, body: bytes, signature_header: str, *, now: int | None = None
    ) -> bool:
        secret = self.config.stripe_webhook_secret
        if not secret:
            return False
        values: dict[str, list[str]] = {}
        for part in signature_header.split(","):
            key, separator, value = part.partition("=")
            if separator:
                values.setdefault(key, []).append(value)
        try:
            timestamp = int(values["t"][0])
        except (KeyError, ValueError):
            return False
        if abs((int(time.time()) if now is None else now) - timestamp) > 300:
            return False
        signed = f"{timestamp}.".encode() + body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, item) for item in values.get("v1", []))

    def apply_event(self, event: dict[str, Any]) -> bool:
        event_type = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata") or {}
            shop_id = metadata.get("shop_id")
            if not shop_id:
                return False
            self.repo.upsert_shop_subscription(
                shop_id=int(shop_id),
                plan=metadata.get("plan", "pro"),
                status="active",
                customer_id=obj.get("customer"),
                subscription_id=obj.get("subscription"),
            )
            return True
        if event_type not in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            return False
        metadata = obj.get("metadata") or {}
        existing = self.repo.get_subscription_by_provider_id(str(obj.get("id", "")))
        shop_id = metadata.get("shop_id") or (existing and existing["shop_id"])
        if not shop_id:
            return False
        period_end = obj.get("current_period_end")
        period_end_iso = (
            datetime.fromtimestamp(int(period_end), UTC).isoformat() if period_end else None
        )
        self.repo.upsert_shop_subscription(
            shop_id=int(shop_id),
            plan=metadata.get("plan") or (existing and existing["plan"]) or "pro",
            status="canceled" if event_type.endswith("deleted") else obj.get("status", "active"),
            customer_id=obj.get("customer"),
            subscription_id=obj.get("id"),
            current_period_end=period_end_iso,
        )
        return True


billing_service = StripeBillingService()
