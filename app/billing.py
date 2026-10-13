from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings, settings
from app.repository import Repository, repository


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
        existing = self.repo.get_shop_subscription(shop["id"])
        if existing.get("stripe_subscription_id") and existing["status"] in {
            "active",
            "trialing",
            "past_due",
            "unpaid",
            "paused",
            "incomplete",
        }:
            raise ValueError("Workspace đã có subscription. Đổi gói trong cổng quản lý thanh toán.")
        price_id = self.price_id(plan)
        if not self.config.stripe_secret_key or not price_id:
            raise RuntimeError("Gói thanh toán này chưa được cấu hình trên Stripe.")
        idempotency_key, cached_url = self.repo.reserve_billing_checkout(shop["id"], plan)
        if cached_url:
            return cached_url
        base_url = self.config.public_base_url.rstrip("/")
        response = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(self.config.stripe_secret_key, ""),
            headers={"Idempotency-Key": idempotency_key},
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
        url = response.json()["url"]
        self.repo.complete_billing_checkout(shop["id"], idempotency_key, url)
        return url

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
        if not self.configured:
            raise RuntimeError("Stripe is not fully configured")
        event_type = event.get("type", "")
        if event_type not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            return False
        event_id = event.get("id")
        data = event.get("data")
        if not isinstance(data, dict):
            raise TypeError("Invalid event data")
        obj = data.get("object")
        if not isinstance(event_id, str) or not re.fullmatch(r"evt_[A-Za-z0-9_]{1,200}", event_id):
            raise ValueError("Invalid event ID")
        if not isinstance(obj, dict):
            raise TypeError("Invalid event object")
        subscription_id = (
            obj.get("subscription") if event_type.startswith("checkout.") else obj.get("id")
        )
        if event_type.startswith("checkout.") and not subscription_id:
            return False  # A payment-mode Checkout does not grant SaaS entitlements.
        if not isinstance(subscription_id, str) or not re.fullmatch(
            r"sub_[A-Za-z0-9_]{1,200}", subscription_id
        ):
            raise ValueError("Invalid subscription ID")
        metadata = obj.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise TypeError("Invalid subscription metadata")
        existing = self.repo.get_subscription_by_provider_id(subscription_id)
        shop_id = metadata.get("shop_id") or (existing and existing["shop_id"])
        if not shop_id:
            return False
        shop_id = int(shop_id)
        if not self.repo.get_shop_by_id(shop_id):
            raise ValueError("Unknown billing workspace")
        revision = self.repo.begin_billing_refresh(shop_id, event_id)
        if revision is None:
            return False
        # Event snapshots can arrive out of order. Fetch the canonical subscription,
        # then commit only if no newer refresh has started for this workspace.
        response = httpx.get(
            f"https://api.stripe.com/v1/subscriptions/{subscription_id}",
            auth=(self.config.stripe_secret_key, ""),
            timeout=10,
        )
        response.raise_for_status()
        canonical = response.json()
        if not isinstance(canonical, dict) or canonical.get("id") != subscription_id:
            raise ValueError("Invalid canonical subscription")
        canonical_metadata = canonical.get("metadata") or {}
        if not isinstance(canonical_metadata, dict) or str(
            canonical_metadata.get("shop_id")
        ) != str(shop_id):
            raise ValueError("Subscription ownership does not match")
        container = canonical.get("items")
        if not isinstance(container, dict):
            raise TypeError("Invalid subscription items")
        items = container.get("data", [])
        if not isinstance(items, list) or len(items) != 1:
            raise ValueError("Subscription must have exactly one ShopPilot plan")
        if not isinstance(items[0], dict) or not isinstance(items[0].get("price"), dict):
            raise TypeError("Invalid subscription price")
        price_id = items[0]["price"].get("id")
        plan = next((name for name in ("pro", "business") if self.price_id(name) == price_id), None)
        if plan is None:
            raise ValueError("Unknown ShopPilot price")
        status = canonical.get("status")
        if status not in {
            "active",
            "trialing",
            "past_due",
            "unpaid",
            "paused",
            "incomplete",
            "incomplete_expired",
            "canceled",
        }:
            raise ValueError("Invalid subscription status")
        period_end = canonical.get("current_period_end") or items[0].get("current_period_end")
        snapshot = {
            "id": subscription_id,
            "created": int(canonical["created"]),
            "plan": plan,
            "status": status,
            "customer": canonical["customer"],
            "current_period_end": datetime.fromtimestamp(int(period_end), UTC).isoformat()
            if period_end
            else None,
        }
        return self.repo.apply_billing_refresh(
            shop_id,
            event_id,
            event_type,
            revision,
            snapshot,
        )


billing_service = StripeBillingService()
