from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.agent as agent_module
import app.auth as auth_module
import app.jobs as jobs_module
import app.main as main_module
from app.agent import SalesAgent
from app.channels.base import InboundMessage
from app.config import settings
from app.database import db_session, json_dumps
from app.inbox import InboxService
from app.jobs import JobWorker
from app.main import app
from app.production import production_errors
from app.repository import repository
from app.tools import ShopTools, confirm_pending_order

OWNER = {
    "display_name": "Release Owner", "email": "release-owner@example.com",
    "password": "release-test-password", "shop_name": "Release Shop",
    "shop_slug": "release-shop", "category": "Retail",
}
PRODUCT = {
    "sku": "NEW-01", "name": "New product", "category": "Retail",
    "description": "A real product", "price": 100000, "stock": 2, "attributes": {},
}


def test_product_lifecycle_and_cross_tenant_boundaries(monkeypatch):
    config = replace(settings, auth_required=True)
    monkeypatch.setattr(main_module, "settings", config)
    monkeypatch.setattr(auth_module, "settings", config)
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=OWNER).status_code == 201
        created = client.post("/api/shops/release-shop/products", json=PRODUCT)
        assert created.status_code == 201
        product_id = created.json()["id"]
        assert client.put(f"/api/shops/mint-fashion/products/{product_id}",
                          json=PRODUCT).status_code == 403
        assert client.put(f"/api/shops/release-shop/products/{product_id}",
                          json={**PRODUCT, "stock": -1}).status_code == 422
        edited = client.put(f"/api/shops/release-shop/products/{product_id}",
                            json={**PRODUCT, "price": 90000, "stock": 5})
        assert edited.json()["stock"] == 5
        assert edited.json()["price"] == 90000
        assert client.delete(f"/api/shops/release-shop/products/{product_id}").json() == {
            "archived": True,
        }
        assert client.get("/api/shops/release-shop/products").json() == []
        assert client.delete(f"/api/shops/release-shop/products/{product_id}").status_code == 404
        with db_session() as connection:
            assert connection.execute("SELECT active FROM products WHERE id = ?",
                                      (product_id,)).fetchone()["active"] == 0


def test_agent_cannot_edit_shop_catalog_or_import_csv():
    with TestClient(app) as client:
        registered = client.post("/api/auth/register", json=OWNER).json()
        shop = repository.get_shop("mint-fashion")
        repository.add_shop_member(shop["id"], registered["user"]["id"], "agent")
        product = repository.list_products(shop["id"])[0]
        assert client.patch("/api/shops/mint-fashion",
                            json={"tagline": "Unapproved edit"}).status_code == 403
        assert client.post("/api/shops/mint-fashion/products", json=PRODUCT).status_code == 403
        assert client.put(f"/api/shops/mint-fashion/products/{product['id']}",
                          json=PRODUCT).status_code == 403
        assert client.delete(f"/api/shops/mint-fashion/products/{product['id']}").status_code == 403
        assert client.post("/api/shops/mint-fashion/products/import", files={
            "file": ("catalog.csv", b"sku,name,category,price,stock\n", "text/csv"),
        }).status_code == 403
        assert client.get("/api/shops/mint-fashion/metrics").status_code == 200


def test_csv_preflight_does_not_partially_write_oversized_or_malformed_file(monkeypatch):
    monkeypatch.setattr(main_module, "settings", replace(settings, max_csv_rows=1))
    with TestClient(app) as client:
        before = client.get("/api/shops/mint-fashion/products").json()
        for content, status in [
            ("sku,name,category,price,stock\nX1,One,Retail,1,1\nX2,Two,Retail,2,2", 413),
            ("sku,name,category,price,stock\nX1,One,Retail,1,1,extra", 400),
        ]:
            response = client.post("/api/shops/mint-fashion/products/import", files={
                "file": ("catalog.csv", content.encode(), "text/csv"),
            })
            assert response.status_code == status
            assert client.get("/api/shops/mint-fashion/products").json() == before
        invalid = client.post("/api/shops/mint-fashion/products/import", files={
            "file": ("catalog.csv", b"sku,name,category,price,stock\nX1,One,Retail,-1,1", "text/csv"),
        }).json()
        assert invalid["imported"] == 0
        assert len(invalid["errors"]) == 1


def prepare_order(conversation_id, shop, product, quantity=1):
    repository.create_conversation(conversation_id, shop["id"])
    return ShopTools(shop, conversation_id).prepare_draft_order(product["id"], quantity)


def test_concurrent_buyers_cannot_oversell_or_make_stock_negative():
    shop = repository.get_shop("mint-fashion")
    product = repository.create_product(shop["id"], {**PRODUCT, "stock": 1})
    prepare_order("buyer-one", shop, product)
    prepare_order("buyer-two", shop, product)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm_pending_order, ["buyer-one", "buyer-two"]))
    assert sum(bool(result and result.get("confirmed")) for result in results) == 1
    assert repository.get_product(shop["id"], product["id"])["stock"] == 0


def test_repeated_concurrent_confirmation_only_decrements_once():
    shop = repository.get_shop("mint-fashion")
    product = repository.create_product(shop["id"], PRODUCT)
    prepare_order("same-order", shop, product)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm_pending_order, ["same-order", "same-order"]))
    assert sum(bool(result and result.get("confirmed")) for result in results) == 1
    assert repository.get_product(shop["id"], product["id"])["stock"] == 1


def test_failed_multi_product_confirmation_rolls_back_all_changes():
    shop = repository.get_shop("mint-fashion")
    product = repository.create_product(shop["id"], PRODUCT)
    unavailable = repository.create_product(shop["id"], {**PRODUCT, "sku": "NONE-01", "stock": 0})
    draft = prepare_order("multi-order", shop, product)
    with db_session() as connection:
        connection.execute("UPDATE draft_orders SET items_json = ? WHERE id = ?", (
            json_dumps([{"product_id": product["id"], "quantity": 1},
                        {"product_id": unavailable["id"], "quantity": 1}]),
            draft["draft_order_id"],
        ))
    assert confirm_pending_order("multi-order")["confirmed"] is False
    assert repository.get_product(shop["id"], product["id"])["stock"] == 2
    assert repository.list_orders(shop["id"])[0]["status"] == "awaiting_confirmation"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["không đồng ý", "chưa xác nhận", "do not confirm",
                                          "yes nhưng tôi không muốn đặt", "xác nhận không?"])
async def test_ambiguous_or_negative_answer_never_confirms_order(message):
    shop = repository.get_shop("mint-fashion")
    product = repository.create_product(shop["id"], PRODUCT)
    prepare_order("no-consent", shop, product)
    await SalesAgent().respond(shop, message, "no-consent")
    assert repository.get_product(shop["id"], product["id"])["stock"] == 2
    assert not any(order["status"] == "confirmed" for order in repository.list_orders(shop["id"]))


@pytest.mark.asyncio
async def test_total_model_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(agent_module, "settings", SimpleNamespace(
        groq_api_key="test-only", llm_turn_timeout_seconds=0.01,
    ))
    agent = SalesAgent()

    async def stalled_model(*args):
        await asyncio.sleep(60)

    monkeypatch.setattr(agent, "_groq_loop", stalled_model)
    result = await asyncio.wait_for(agent.respond(repository.get_shop("mint-fashion"),
                                                 "Xin chào"), timeout=1)
    assert result["model"] == "rule-fallback"
    assert result["message"]


@pytest.mark.asyncio
async def test_failed_delivery_resumes_without_second_ai_call_or_duplicate_input():
    class FakeAgent:
        calls = 0

        async def respond(self, *args):
            self.calls += 1
            return {"status": "handoff", "message": "Đã chuyển nhân viên", "model": "test"}

    class FlakyAdapter:
        calls = 0

        async def send_text(self, recipient, text):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("temporary outage")
            return {"message_id": "provider-reply"}

    agent, adapter = FakeAgent(), FlakyAdapter()
    service = InboxService(agent=agent)
    shop = repository.get_shop("mint-fashion")
    event = InboundMessage(channel="messenger", external_event_id="retry-message",
                           external_conversation_id="page:customer", external_customer_id="customer",
                           text="Cho tôi gặp nhân viên", metadata={})
    with pytest.raises(httpx.ConnectError):
        await service.process(shop, event, adapter)
    result = await service.process(shop, event, adapter)
    assert result["replied"] is True
    assert agent.calls == 1
    assert adapter.calls == 2
    messages = repository.list_channel_messages(result["channel_conversation_id"])
    assert len(messages) == 2
    assert messages[1]["metadata"]["provider_message_id"] == "provider-reply"
    assert (await service.process(shop, event, adapter))["status"] == "duplicate"
    assert adapter.calls == 2


def test_trusted_job_can_resume_received_event_after_process_restart():
    shop = repository.get_shop("mint-fashion")
    assert repository.record_channel_event(shop["id"], "messenger", "crashed", "message", {})
    assert not repository.record_channel_event(shop["id"], "messenger", "crashed", "message", {})
    assert repository.record_channel_event(shop["id"], "messenger", "crashed", "message", {},
                                           resume=True)
    repository.mark_channel_event("messenger", "crashed", "processed")
    assert not repository.record_channel_event(shop["id"], "messenger", "crashed", "message", {},
                                               resume=True)


@pytest.mark.asyncio
async def test_worker_recovers_from_polling_failure_and_shutdown_is_bounded(monkeypatch):
    worker = JobWorker(poll_seconds=0.01)
    calls = 0

    async def flaky_poll():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily down")
        worker._stop.set()
        return False

    monkeypatch.setattr(worker, "run_once", flaky_poll)
    await asyncio.wait_for(worker.run(), timeout=1)
    assert calls == 2
    monkeypatch.setattr(jobs_module, "settings", SimpleNamespace(job_shutdown_seconds=0.01))
    task = asyncio.create_task(asyncio.sleep(60))
    await asyncio.wait_for(worker.shutdown(task), timeout=1)
    assert task.cancelled()


def test_readiness_fails_when_database_or_worker_is_unavailable(monkeypatch):
    with TestClient(app) as client:
        assert client.get("/api/health/ready").status_code == 200
        monkeypatch.setattr(main_module, "health", lambda: (_ for _ in ()).throw(RuntimeError()))
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False
        assert response.json()["database"] == "unavailable"


def test_production_config_fails_closed_and_does_not_disclose_secrets():
    invalid = replace(settings, app_env="production", auth_required=False, database_url=None,
                      public_base_url="http://localhost", token_encryption_key="private-short")
    errors = production_errors(invalid)
    assert len(errors) >= 4
    assert "private-short" not in " ".join(errors)
    valid = replace(invalid, auth_required=True, database_url="postgresql://db/shop",
                    public_base_url="https://shop.example", token_encryption_key="x" * 32,
                    rate_limit_enabled=True, email_verification_required=False)
    assert production_errors(valid) == []


def test_cross_origin_console_writes_are_denied_but_widget_stays_embeddable():
    with TestClient(app) as client:
        denied = client.post("/api/auth/register", json=OWNER,
                             headers={"Origin": "https://attacker.example"})
        assert denied.status_code == 403
        allowed = client.post("/api/auth/register", json=OWNER,
                              headers={"Origin": "http://testserver"})
        assert allowed.status_code == 201
        widget = client.post("/api/channels/web/mint-fashion/messages", json={"message": "Hi"},
                             headers={"Origin": "https://store.example"})
        assert widget.status_code == 200
        assert allowed.headers["Cache-Control"] == "no-store"


def test_other_shop_does_not_report_global_page_as_connected(monkeypatch):
    config = replace(settings, meta_app_secret="app-secret", meta_page_access_token="page-token",
                     meta_page_id="test-page", meta_verify_token="verify")
    import app.channels.meta as meta_module
    monkeypatch.setattr(main_module, "settings", config)
    monkeypatch.setattr(meta_module, "settings", config)
    with TestClient(app) as client:
        connected = client.get("/api/integrations/meta/status?shop_slug=mint-fashion").json()
        other = client.get("/api/integrations/meta/status?shop_slug=lumi-beauty").json()
    assert connected["configured"] is True
    assert connected["delivery_verified"] is False
    assert other["configured"] is False


def test_monthly_ai_budget_is_atomic_and_resets_for_new_period():
    shop = repository.get_shop("mint-fashion")
    with ThreadPoolExecutor(max_workers=4) as executor:
        reserved = list(executor.map(
            lambda _: repository.reserve_ai_turn(shop["id"], "2026-10", 1), range(4),
        ))
    assert reserved.count(True) == 1
    assert repository.ai_usage(shop["id"], "2026-10") == 1
    assert repository.reserve_ai_turn(shop["id"], "2026-11", 1)


@pytest.mark.asyncio
async def test_exhausted_ai_budget_hands_off_without_calling_model(monkeypatch):
    from app.usage import usage_status

    shop = repository.get_shop("mint-fashion")
    usage = usage_status(shop["id"])
    with db_session() as connection:
        connection.execute("INSERT INTO shop_ai_usage (shop_id, period, used) VALUES (?, ?, ?)",
                           (shop["id"], usage["period"], usage["limit"]))
    agent = SalesAgent()

    async def must_not_call_model(*args):
        raise AssertionError("Exhausted budget must not reach the model")

    monkeypatch.setattr(agent, "_groq_loop", must_not_call_model)
    result = await agent.respond(shop, "Xin chào")
    assert result["model"] == "usage-guard"
    assert result["status"] == "handoff"
    assert usage_status(shop["id"])["used"] == usage["limit"]


def test_canceled_paid_plan_uses_free_budget():
    from app.usage import usage_status

    shop = repository.get_shop("mint-fashion")
    repository.upsert_shop_subscription(shop_id=shop["id"], plan="business", status="canceled",
                                       customer_id=None, subscription_id=None)
    assert usage_status(shop["id"])["effective_plan"] == "free"
    assert usage_status(shop["id"])["limit"] == 500


def test_dashboard_chat_requires_membership_but_widget_remains_public(monkeypatch):
    config = replace(settings, auth_required=True)
    monkeypatch.setattr(main_module, "settings", config)
    monkeypatch.setattr(auth_module, "settings", config)
    with TestClient(app) as client:
        assert client.post("/api/shops/mint-fashion/chat", json={"message": "Hi"}).status_code == 401
        client.post("/api/auth/register", json=OWNER)
        assert client.post("/api/shops/mint-fashion/chat", json={"message": "Hi"}).status_code == 403
        assert client.post("/api/shops/release-shop/chat", json={"message": "Hi"}).status_code == 200
        assert client.post("/api/channels/web/mint-fashion/messages",
                           json={"message": "Hi"}).status_code == 200


def test_widget_ids_are_isolated_even_when_customers_choose_same_id():
    with TestClient(app) as client:
        for slug in ["mint-fashion", "lumi-beauty"]:
            response = client.post(f"/api/channels/web/{slug}/messages",
                                   json={"message": "Hi", "conversation_id": "same-client-id"})
            assert response.status_code == 200
            assert response.json()["conversation_id"] == "same-client-id"
            history = client.get(f"/api/channels/web/{slug}/conversations/same-client-id/messages")
            assert history.status_code == 200
            assert len(history.json()) == 2


@pytest.mark.asyncio
async def test_staff_takeover_during_generation_suppresses_ai_delivery():
    class TakeoverAgent:
        async def respond(self, shop, message, internal_id):
            conversation = repository.list_channel_conversations(shop["id"])[0]
            repository.set_channel_bot(conversation["id"], False, "Human staff")
            return {"status": "active", "message": "Late AI response", "model": "test"}

    class NeverSend:
        async def send_text(self, *args):
            raise AssertionError("AI must not send after staff took over")

    event = InboundMessage(channel="web", external_event_id="takeover-race",
                           external_conversation_id="takeover", external_customer_id="web-client",
                           text="Hello", metadata={})
    result = await InboxService(agent=TakeoverAgent()).process(
        repository.get_shop("mint-fashion"), event, NeverSend(),
    )
    assert result["replied"] is False
    assert result["status"] == "waiting_for_human"
