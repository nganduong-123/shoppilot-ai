import hashlib
import hmac
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.billing as billing_module
from app.billing import StripeBillingService, billing_service
from app.main import app
from app.repository import repository

ACCOUNT = {
    "display_name": "Billing Owner",
    "email": "billing-owner@example.com",
    "password": "billing-password-123",
    "shop_name": "Billing Shop",
    "shop_slug": "billing-shop",
    "category": "Retail",
}


def billing_config(**overrides):
    defaults = {
        "stripe_secret_key": "sk_test_local",
        "stripe_webhook_secret": "whsec_local",
        "stripe_price_pro": "price_pro",
        "stripe_price_business": "price_business",
        "public_base_url": "https://example.test",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def stripe_signature(body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(b"whsec_local", f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_signature_verification_rejects_tampering_and_expired_payloads():
    service = StripeBillingService(config=billing_config())
    body = b'{"id":"evt_test"}'
    signature = stripe_signature(body, 1_700_000_000)

    assert service.verify_signature(body, signature, now=1_700_000_000)
    assert not service.verify_signature(body + b" ", signature, now=1_700_000_000)
    assert not service.verify_signature(body, signature, now=1_700_000_301)


def test_checkout_posts_expected_subscription_metadata(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"url": "https://checkout.stripe.test/session"}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(billing_module.httpx, "post", fake_post)
    service = StripeBillingService(config=billing_config())
    url = service.create_checkout(shop={"id": 42}, user={"email": "owner@example.com"}, plan="pro")

    assert url == "https://checkout.stripe.test/session"
    assert captured["data"]["line_items[0][price]"] == "price_pro"
    assert captured["data"]["metadata[shop_id]"] == "42"
    assert captured["data"]["mode"] == "subscription"


def test_billing_api_defaults_to_free_and_requires_an_owner_for_checkout():
    with TestClient(app) as client:
        client.post("/api/auth/register", json=ACCOUNT)
        result = client.get("/api/shops/billing-shop/billing")
        checkout = client.post("/api/shops/billing-shop/billing/checkout", json={"plan": "pro"})

    assert result.status_code == 200
    assert result.json()["subscription"]["plan"] == "free"
    assert result.json()["can_manage"] is True
    assert checkout.status_code == 503


def test_non_owner_cannot_start_checkout():
    agent_account = {
        **ACCOUNT,
        "display_name": "Billing Agent",
        "email": "billing-agent@example.com",
        "shop_name": "Agent Shop",
        "shop_slug": "billing-agent-shop",
    }
    with TestClient(app) as owner_client:
        owner_client.post("/api/auth/register", json=ACCOUNT)
    with TestClient(app) as agent_client:
        agent_client.post("/api/auth/register", json=agent_account)
        shop = repository.get_shop("billing-shop")
        agent = repository.get_user_by_email(agent_account["email"])
        repository.add_shop_member(shop["id"], agent["id"], "agent")
        denied = agent_client.post(
            "/api/shops/billing-shop/billing/checkout", json={"plan": "pro"}
        )

    assert denied.status_code == 403


def test_signed_checkout_webhook_activates_shop_subscription(monkeypatch):
    monkeypatch.setattr(billing_service, "config", billing_config())
    with TestClient(app) as client:
        client.post("/api/auth/register", json=ACCOUNT)
        shop = repository.get_shop("billing-shop")
        event = {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test",
                    "subscription": "sub_test",
                    "metadata": {"shop_id": str(shop["id"]), "plan": "pro"},
                }
            },
        }
        body = json.dumps(event, separators=(",", ":")).encode()
        response = client.post(
            "/api/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": stripe_signature(body)},
        )
        subscription = repository.get_shop_subscription(shop["id"])

    assert response.status_code == 200
    assert response.json() == {"received": True, "applied": True}
    assert subscription["plan"] == "pro"
    assert subscription["stripe_customer_id"] == "cus_test"
