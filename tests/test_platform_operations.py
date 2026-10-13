from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.billing as billing_module
from app.billing import StripeBillingService
from app.database import db_session
from app.jobs import job_worker
from app.main import app
from app.repository import SeatLimitReached, repository
from app.usage import usage_status

ACCOUNT = {
    "display_name": "Platform Operator",
    "email": "operator@example.com",
    "password": "operator-test-password",
    "shop_name": "Operator Shop",
    "shop_slug": "operator-shop",
    "category": "Retail",
}
OTHER = {
    **ACCOUNT,
    "email": "customer@example.com",
    "display_name": "Customer",
    "shop_name": "Customer Shop",
    "shop_slug": "customer-shop",
}


def register(client, payload=ACCOUNT):
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    return response.json()["user"]


def config():
    return SimpleNamespace(
        stripe_secret_key="test-only-key",
        stripe_webhook_secret="test-signature",
        stripe_price_pro="price_pro",
        stripe_price_business="price_business",
    )


def event(shop, event_id="evt_first", sub_id="sub_primary", kind="customer.subscription.updated"):
    return {
        "id": event_id,
        "type": kind,
        "data": {
            "object": {
                "id": sub_id,
                "subscription": sub_id,
                "metadata": {"shop_id": str(shop["id"])},
            }
        },
    }


def canonical(shop, status="active", sub_id="sub_primary", created=100):
    return {
        "id": sub_id,
        "created": created,
        "status": status,
        "customer": "cus_demo",
        "metadata": {"shop_id": str(shop["id"])},
        "items": {"data": [{"price": {"id": "price_pro"}, "current_period_end": 1900000000}]},
    }


def fake_response(data):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)


def test_owner_is_not_platform_admin_and_public_signup_cannot_elevate_role():
    with TestClient(app) as client:
        assert client.get("/api/admin/overview").status_code == 401
        register(client, {**ACCOUNT, "platform_admin": True, "role": "admin"})
        assert client.get("/api/auth/me").json()["platform_admin"] is False
        for path in [
            "/admin",
            "/api/admin/overview",
            "/api/admin/readiness",
            "/api/admin/users",
            "/api/admin/shops",
            "/api/admin/jobs",
            "/api/admin/audit",
        ]:
            assert client.get(path).status_code == 403


def test_verified_operator_bootstrap_and_admin_views_never_expose_credentials():
    with TestClient(app) as client:
        user = register(client)
        assert (
            repository.grant_platform_admin(ACCOUNT["email"], "Verified operator bootstrap")
            == user["id"]
        )
        assert client.get("/api/auth/me").json()["platform_admin"] is True
        assert client.get("/admin").status_code == 200
        assert client.get("/api/admin/overview").json()["users"] == 1
        readiness = client.get("/api/admin/readiness")
        assert readiness.status_code == 200
        assert {item["key"] for item in readiness.json()["checks"]} == {
            "production",
            "database",
            "ai",
            "email",
            "meta",
            "stripe",
            "fulfillment",
        }
        assert "secret" not in readiness.text.casefold()
        assert "token" not in readiness.text.casefold()
        users = client.get("/api/admin/users")
        assert "password" not in users.text
        assert "token" not in users.text
        assert users.json()[0]["platform_admin"] == 1
        assert client.get("/api/admin/users?limit=101").status_code == 422
        assert client.get("/api/admin/audit").json()[0]["action"] == "operator.grant_admin"


def test_admin_status_change_revokes_sessions_and_protects_admin_accounts():
    with TestClient(app) as operator:
        admin = register(operator)
        repository.grant_platform_admin(ACCOUNT["email"], "Verified bootstrap")
        # Separate cookie jars, one application lifespan/worker/event loop.
        customer = TestClient(app)
        user = register(customer, OTHER)
        suspended = operator.patch(
            f"/api/admin/users/{user['id']}/status",
            json={"status": "suspended", "reason": "Confirmed abuse complaint"},
        )
        assert suspended.status_code == 200
        assert customer.get("/api/auth/me").json()["authenticated"] is False
        denied = customer.post(
            "/api/auth/login", json={"email": OTHER["email"], "password": OTHER["password"]}
        )
        assert denied.status_code == 401
        customer.close()
        assert (
            operator.patch(
                f"/api/admin/users/{admin['id']}/status",
                json={"status": "suspended", "reason": "Must not lock admin"},
            ).status_code
            == 409
        )
        assert (
            operator.patch(
                f"/api/admin/users/{user['id']}/status",
                json={"status": "active", "reason": "Complaint investigated and resolved"},
            ).status_code
            == 200
        )
        actions = [row["action"] for row in operator.get("/api/admin/audit").json()]
        assert "user.suspended" in actions and "user.active" in actions


def test_admin_trial_has_expiry_changes_entitlements_and_does_not_downgrade_paid_plan():
    with TestClient(app) as client:
        register(client)
        repository.grant_platform_admin(ACCOUNT["email"], "Verified bootstrap")
        shop = repository.get_shop("operator-shop")
        granted = client.post(
            f"/api/admin/shops/{shop['id']}/trial",
            json={"plan": "pro", "days": 7, "reason": "Founder supervised trial"},
        )
        assert granted.status_code == 200
        assert usage_status(shop["id"])["effective_plan"] == "pro"
        assert repository.seat_limits(shop["id"])["limit"] == 5
        assert (
            client.post(
                f"/api/admin/shops/{shop['id']}/trial",
                json={"plan": "business", "days": 365, "reason": "Invalid duration"},
            ).status_code
            == 422
        )
        repository.upsert_shop_subscription(
            shop_id=shop["id"],
            plan="business",
            status="active",
            customer_id=None,
            subscription_id=None,
        )
        assert usage_status(shop["id"])["effective_plan"] == "business"
        with db_session() as connection:
            connection.execute(
                "UPDATE shop_plan_trials SET expires_at = ? WHERE shop_id = ?",
                ("2000-01-01T00:00:00+00:00", shop["id"]),
            )
        repository.upsert_shop_subscription(
            shop_id=shop["id"],
            plan="business",
            status="canceled",
            customer_id=None,
            subscription_id=None,
        )
        assert usage_status(shop["id"])["effective_plan"] == "free"


def test_admin_requeues_only_failed_supported_jobs_with_audit(monkeypatch):
    async def idle_worker():
        return False

    # Assert queue transitions without racing the independently tested consumer.
    monkeypatch.setattr(job_worker, "run_once", idle_worker)
    with TestClient(app) as client:
        register(client)
        repository.grant_platform_admin(ACCOUNT["email"], "Verified bootstrap")
        repository.enqueue_job(
            "commerce_order", {"private_customer_data": "not for admin API"}, max_attempts=1
        )
        job = repository.claim_job()
        repository.fail_job(job["id"], "provider unavailable", 1, 1)
        listing = client.get("/api/admin/jobs")
        assert "private_customer_data" not in listing.text
        assert "payload" not in listing.text
        assert (
            client.post(
                f"/api/admin/jobs/{job['id']}/retry",
                json={"reason": "Provider connection restored"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/admin/jobs/{job['id']}/retry", json={"reason": "Do not enqueue twice"}
            ).status_code
            == 409
        )
        with db_session() as connection:
            row = connection.execute(
                "SELECT status,attempts FROM jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert row["status"] == "queued" and row["attempts"] == 0


def test_cross_origin_admin_write_is_denied():
    with TestClient(app) as client:
        register(client)
        repository.grant_platform_admin(ACCOUNT["email"], "Verified bootstrap")
        shop = repository.get_shop("operator-shop")
        assert (
            client.post(
                f"/api/admin/shops/{shop['id']}/trial",
                json={"plan": "pro", "reason": "CSRF attempt blocked"},
                headers={"Origin": "https://attacker.example"},
            ).status_code
            == 403
        )


def test_free_seats_block_invites_and_pro_reservations_are_atomic():
    with TestClient(app) as client:
        user = register(client)
        assert (
            client.post(
                "/api/shops/operator-shop/team/invitations",
                json={"email": "first@example.com", "role": "agent"},
            ).status_code
            == 409
        )
        shop = repository.get_shop("operator-shop")
        repository.upsert_shop_subscription(
            shop_id=shop["id"], plan="pro", status="active", customer_id=None, subscription_id=None
        )

        def invite(number):
            try:
                repository.create_shop_invitation(
                    shop_id=shop["id"],
                    email=f"seat{number}@example.com",
                    role="agent",
                    token_hash=f"seat-hash-{number}",
                    expires_at="2099-01-01",
                    invited_by=user["id"],
                )
                return True
            except SeatLimitReached:
                return False

        with ThreadPoolExecutor(max_workers=6) as executor:
            reserved = list(executor.map(invite, range(6)))
        assert reserved.count(True) == 4
        assert repository.seat_limits(shop["id"])["pending"] == 4


def test_duplicate_and_stale_stripe_events_use_canonical_current_status(monkeypatch):
    with TestClient(app) as client:
        register(client)
    shop = repository.get_shop("operator-shop")
    current = canonical(shop)
    calls = []
    monkeypatch.setattr(
        billing_module.httpx,
        "get",
        lambda *args, **kwargs: (calls.append(1), fake_response(current))[1],
    )
    service = StripeBillingService(config=config())
    assert service.apply_event(event(shop)) is True
    assert service.apply_event(event(shop)) is False
    assert len(calls) == 1
    current["status"] = "canceled"
    assert service.apply_event(event(shop, "evt_canceled"))
    # A late 'created' event still retrieves the current canceled subscription.
    assert service.apply_event(event(shop, "evt_late", kind="customer.subscription.created"))
    assert usage_status(shop["id"])["effective_plan"] == "free"


def test_old_subscription_does_not_replace_newer_subscription(monkeypatch):
    with TestClient(app) as client:
        register(client)
    shop = repository.get_shop("operator-shop")
    service = StripeBillingService(config=config())
    current = canonical(shop, sub_id="sub_new", created=200)
    monkeypatch.setattr(billing_module.httpx, "get", lambda *args, **kwargs: fake_response(current))
    assert service.apply_event(event(shop, "evt_new", "sub_new"))
    current = canonical(shop, status="canceled", sub_id="sub_old", created=100)
    assert service.apply_event(event(shop, "evt_old", "sub_old")) is False
    assert repository.get_shop_subscription(shop["id"])["stripe_subscription_id"] == "sub_new"
    assert repository.get_shop_subscription(shop["id"])["status"] == "active"


def test_failed_stripe_fetch_can_retry_and_stale_concurrent_snapshot_is_rejected(monkeypatch):
    with TestClient(app) as client:
        register(client)
    shop = repository.get_shop("operator-shop")
    service = StripeBillingService(config=config())

    def failed_get(*args, **kwargs):
        raise httpx.ConnectError("temporary provider outage")

    monkeypatch.setattr(billing_module.httpx, "get", failed_get)
    with pytest.raises(httpx.ConnectError):
        service.apply_event(event(shop))
    monkeypatch.setattr(
        billing_module.httpx, "get", lambda *args, **kwargs: fake_response(canonical(shop))
    )
    assert service.apply_event(event(shop))
    earlier = repository.begin_billing_refresh(shop["id"], "evt_earlier")
    later = repository.begin_billing_refresh(shop["id"], "evt_later")
    snapshot = {
        "id": "sub_primary",
        "created": 100,
        "plan": "pro",
        "status": "canceled",
        "customer": "cus_demo",
        "current_period_end": None,
    }
    with pytest.raises(RuntimeError, match="newer"):
        repository.apply_billing_refresh(shop["id"], "evt_earlier", "updated", earlier, snapshot)
    assert repository.apply_billing_refresh(shop["id"], "evt_later", "updated", later, snapshot)
    assert repository.get_shop_subscription(shop["id"])["status"] == "canceled"


def test_price_and_ownership_mismatch_never_grant_entitlements(monkeypatch):
    with TestClient(app) as client:
        register(client)
    shop = repository.get_shop("operator-shop")
    current = canonical(shop)
    current["items"]["data"][0]["price"]["id"] = "unrecognized_price"
    monkeypatch.setattr(billing_module.httpx, "get", lambda *args, **kwargs: fake_response(current))
    with pytest.raises(ValueError, match="price"):
        StripeBillingService(config=config()).apply_event(event(shop))
    current["metadata"]["shop_id"] = "9999"
    with pytest.raises(ValueError, match="ownership"):
        StripeBillingService(config=config()).apply_event(event(shop))
    assert repository.get_shop_subscription(shop["id"])["plan"] == "free"
