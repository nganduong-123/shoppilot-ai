from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.channels.meta as meta_module
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


def test_meta_signed_request_verification():
    adapter = MetaMessengerAdapter(meta_config())
    payload = {"algorithm": "HMAC-SHA256", "user_id": "customer-delete"}
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        b"test-secret", encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    assert adapter.parse_signed_request(
        f"{encoded_signature}.{encoded_payload}"
    )["user_id"] == "customer-delete"

    tampered_signature = encoded_signature[:-1] + (
        "A" if encoded_signature[-1] != "A" else "B"
    )
    with pytest.raises(ValueError, match="Chữ ký"):
        adapter.parse_signed_request(f"{tampered_signature}.{encoded_payload}")


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


def test_customer_data_deletion_removes_only_matching_platform_user():
    shop = repository.get_shop("mint-fashion")
    assert shop is not None
    connection = repository.upsert_channel_connection(
        shop["id"], "messenger", "page-123", "Test Page"
    )
    target = repository.get_or_create_channel_conversation(
        shop_id=shop["id"],
        connection_id=connection["id"],
        channel="messenger",
        external_conversation_id="page-123:customer-delete",
        external_customer_id="customer-delete",
    )
    survivor = repository.get_or_create_channel_conversation(
        shop_id=shop["id"],
        connection_id=connection["id"],
        channel="messenger",
        external_conversation_id="page-123:customer-keep",
        external_customer_id="customer-keep",
    )
    repository.add_channel_message(
        target["id"],
        external_message_id="delete-mid",
        direction="inbound",
        sender_type="customer",
        content="Please delete this",
        status="received",
    )
    repository.record_channel_event(
        shop["id"],
        "messenger",
        "delete-event",
        "message",
        {"sender": {"id": "customer-delete"}},
    )

    receipt = repository.delete_external_customer_data(
        channel="messenger",
        external_customer_id="customer-delete",
        confirmation_code="delete-confirmation",
    )

    assert receipt["status"] == "completed"
    assert receipt["deleted_records"] >= 4
    assert repository.get_channel_conversation(target["id"]) is None
    assert repository.get_channel_conversation(survivor["id"]) is not None
    assert repository.get_data_deletion_status("delete-confirmation")["status"] == "completed"


def test_meta_data_deletion_callback_returns_trackable_receipt(monkeypatch):
    monkeypatch.setattr(meta_module, "settings", meta_config())
    payload = {"algorithm": "HMAC-SHA256", "user_id": "callback-customer"}
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        b"test-secret", encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    with TestClient(app) as client:
        response = client.post(
            "/api/meta/data-deletion",
            data={"signed_request": f"{encoded_signature}.{encoded_payload}"},
        )
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["url"].endswith(f"?code={receipt['confirmation_code']}")

        status = client.get(
            f"/api/data-deletion/status/{receipt['confirmation_code']}"
        )

    assert status.status_code == 200
    assert status.json()["status"] == "completed"


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
