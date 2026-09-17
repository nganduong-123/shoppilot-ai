from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.channels.meta import MetaMessengerAdapter
from app.main import app
from app.repository import repository


def meta_config(**overrides):
    values = {
        "meta_app_secret": "test-secret",
        "meta_page_access_token": "page-token",
        "meta_verify_token": "verify-me",
        "meta_page_id": "page-123",
        "meta_graph_api_version": "v23.0",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_meta_signature_and_challenge_verification():
    adapter = MetaMessengerAdapter(meta_config())
    body = b'{"object":"page"}'
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    assert adapter.configured is True
    assert adapter.verify_signature(body, signature) is True
    assert adapter.verify_signature(body + b"x", signature) is False
    assert adapter.verify_challenge("subscribe", "verify-me") is True
    assert adapter.verify_challenge("subscribe", "wrong") is False


def test_meta_parser_ignores_echo_and_extracts_customer_message():
    adapter = MetaMessengerAdapter(meta_config())
    payload = {
        "object": "page",
        "entry": [{
            "id": "page-123",
            "messaging": [
                {
                    "sender": {"id": "customer-9"},
                    "timestamp": 100,
                    "message": {"mid": "mid-1", "text": "Còn size M không?"},
                },
                {
                    "sender": {"id": "page-123"},
                    "message": {"mid": "mid-2", "text": "echo", "is_echo": True},
                },
            ],
        }],
    }

    events = adapter.parse_events(payload)

    assert len(events) == 1
    assert events[0].external_event_id == "mid-1"
    assert events[0].external_conversation_id == "page-123:customer-9"
    assert events[0].text == "Còn size M không?"


def test_repeated_platform_event_is_idempotent():
    shop = repository.get_shop("mint-fashion")
    assert shop is not None

    first = repository.record_channel_event(
        shop["id"], "messenger", "same-mid", "message", {"text": "hello"}
    )
    repeated = repository.record_channel_event(
        shop["id"], "messenger", "same-mid", "message", {"text": "hello"}
    )

    assert first is True
    assert repeated is False


def test_web_widget_message_appears_in_unified_inbox_and_bot_can_be_paused():
    with TestClient(app) as client:
        first = client.post(
            "/api/channels/web/mint-fashion/messages",
            json={"message": "Tìm áo sơ mi trắng", "customer_name": "Khách web"},
        )
        assert first.status_code == 200
        body = first.json()
        assert body["replied"] is True
        assert body["conversation_id"]

        conversations = client.get("/api/shops/mint-fashion/inbox/conversations").json()
        assert len(conversations) == 1
        inbox_id = conversations[0]["id"]
        assert conversations[0]["channel"] == "web"

        messages = client.get(
            f"/api/shops/mint-fashion/inbox/conversations/{inbox_id}/messages"
        ).json()
        assert [message["direction"] for message in messages] == ["inbound", "outbound"]

        paused = client.post(
            f"/api/shops/mint-fashion/inbox/conversations/{inbox_id}/bot",
            json={"enabled": False, "assigned_to": "Dương Thị Ngân"},
        )
        assert paused.status_code == 200
        assert paused.json()["bot_enabled"] is False

        second = client.post(
            "/api/channels/web/mint-fashion/messages",
            json={
                "message": "Tôi cần gặp nhân viên",
                "conversation_id": body["conversation_id"],
            },
        )
        assert second.status_code == 200
        assert second.json()["replied"] is False
        assert second.json()["status"] == "waiting_for_human"

        reply = client.post(
            f"/api/shops/mint-fashion/inbox/conversations/{inbox_id}/messages",
            json={"message": "Chào bạn, mình tiếp nhận tư vấn nhé.", "agent_name": "Ngân"},
        )
        assert reply.status_code == 201
        assert reply.json()["sender_type"] == "human"

        history = client.get(
            f"/api/channels/web/mint-fashion/conversations/{body['conversation_id']}/messages"
        )
        assert history.status_code == 200
        assert history.json()[-1]["sender_type"] == "human"


def test_inbox_conversation_is_tenant_isolated():
    with TestClient(app) as client:
        client.post(
            "/api/channels/web/mint-fashion/messages",
            json={"message": "Tìm áo", "customer_name": "Khách A"},
        )
        conversation = client.get(
            "/api/shops/mint-fashion/inbox/conversations"
        ).json()[0]
        response = client.get(
            f"/api/shops/lumi-beauty/inbox/conversations/{conversation['id']}/messages"
        )

    assert response.status_code == 404
