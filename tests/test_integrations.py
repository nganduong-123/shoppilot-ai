from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx

from app.integrations import CommerceConnector


def connector_config(**overrides):
    values = {
        "shipping_quote_url": "https://shipping.example/quote",
        "commerce_order_webhook_url": "https://commerce.example/orders",
        "integration_webhook_secret": "integration-secret",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_shipping_quote_uses_signed_provider_response(monkeypatch):
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured.update(url=url, content=content, headers=headers, timeout=timeout)
        return httpx.Response(
            200,
            json={"fee": 22000, "eta": "1 ngày", "provider": "GHN bridge"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    connector = CommerceConnector(connector_config())

    result = connector.quote_shipping(
        shop_slug="mint-fashion",
        location="Quận 1, TP.HCM",
        order_value=300000,
        currency="VND",
    )

    expected = hmac.new(
        b"integration-secret", captured["content"], hashlib.sha256
    ).hexdigest()
    assert result["fee"] == 22000
    assert result["source"] == "shipping_provider"
    assert captured["headers"]["X-ShopPilot-Signature"] == f"sha256={expected}"


def test_connector_serialization_is_stable_for_signature():
    connector = CommerceConnector(connector_config())
    first = connector._body({"b": 2, "a": "á"})
    second = connector._body({"a": "á", "b": 2})

    assert first == second
    assert json.loads(first) == {"a": "á", "b": 2}
