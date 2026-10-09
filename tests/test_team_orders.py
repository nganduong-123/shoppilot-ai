from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.main import app
from app.repository import repository
from app.tools import ShopTools, confirm_pending_order

OWNER = {
    "display_name": "Owner Team",
    "email": "team-owner@example.com",
    "password": "owner-password-123",
    "shop_name": "Team Shop",
    "shop_slug": "team-shop",
    "category": "Retail",
}

AGENT = {
    "display_name": "Agent Team",
    "email": "team-agent@example.com",
    "password": "agent-password-123",
    "shop_name": "Agent Personal Shop",
    "shop_slug": "agent-personal-shop",
    "category": "Retail",
}


def test_owner_can_invite_member_and_member_accepts_only_with_matching_email():
    with TestClient(app) as owner_client:
        owner_client.post("/api/auth/register", json=OWNER)
        invited = owner_client.post(
            "/api/shops/team-shop/team/invitations",
            json={"email": AGENT["email"], "role": "agent"},
        )
        team_before = owner_client.get("/api/shops/team-shop/team")

    assert invited.status_code == 201
    assert invited.json()["email"] == AGENT["email"]
    assert len(team_before.json()["invitations"]) == 1
    token = parse_qs(urlparse(invited.json()["invite_url"]).query)["invite"][0]

    with TestClient(app) as agent_client:
        agent_client.post("/api/auth/register", json=AGENT)
        accepted = agent_client.post(f"/api/invitations/{token}/accept")
        agent_team = agent_client.get("/api/shops/team-shop/team")

    assert accepted.status_code == 200
    assert accepted.json()["shop"]["role"] == "agent"
    assert agent_team.status_code == 200
    assert agent_team.json()["current_role"] == "agent"
    assert agent_team.json()["invitations"] == []

    with TestClient(app) as owner_client:
        owner_client.post(
            "/api/auth/login",
            json={"email": OWNER["email"], "password": OWNER["password"]},
        )
        team_after = owner_client.get("/api/shops/team-shop/team")
        agent_id = next(
            member["user_id"]
            for member in team_after.json()["members"]
            if member["email"] == AGENT["email"]
        )
        promoted = owner_client.patch(
            f"/api/shops/team-shop/team/members/{agent_id}",
            json={"role": "manager"},
        )

    assert team_after.json()["invitations"] == []
    assert promoted.json()["role"] == "manager"


def test_invitation_cannot_be_accepted_by_a_different_email():
    with TestClient(app) as owner_client:
        owner_client.post("/api/auth/register", json=OWNER)
        invited = owner_client.post(
            "/api/shops/team-shop/team/invitations",
            json={"email": "expected@example.com", "role": "agent"},
        ).json()
    token = parse_qs(urlparse(invited["invite_url"]).query)["invite"][0]

    with TestClient(app) as wrong_client:
        wrong_client.post("/api/auth/register", json=AGENT)
        rejected = wrong_client.post(f"/api/invitations/{token}/accept")

    assert rejected.status_code == 400


def test_confirmed_orders_can_be_managed_from_the_backoffice():
    with TestClient(app) as client:
        client.post("/api/auth/register", json=OWNER)
        shop = repository.get_shop("team-shop")
        product = repository.create_product(
            shop["id"],
            {
                "sku": "TEAM-01",
                "name": "Sản phẩm Team",
                "category": "Retail",
                "description": "Sản phẩm dùng kiểm thử đơn hàng.",
                "price": 150000,
                "stock": 5,
                "attributes": {},
            },
        )
        repository.create_conversation("team-order", shop["id"])
        tools = ShopTools(shop, "team-order")
        draft = tools.execute(
            "prepare_draft_order",
            {"product_id": product["id"], "quantity": 1, "location": "Hà Nội"},
        )
        confirm_pending_order("team-order")

        orders = client.get("/api/shops/team-shop/orders")
        fulfilled = client.patch(
            f"/api/shops/team-shop/orders/{draft['draft_order_id']}/fulfillment",
            json={
                "status": "shipped",
                "tracking_code": "VN123456",
                "note": "Đã bàn giao đơn vị vận chuyển",
            },
        )
        refreshed = client.get("/api/shops/team-shop/orders")

    assert orders.status_code == 200
    assert orders.json()[0]["fulfillment_status"] == "processing"
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "shipped"
    assert refreshed.json()[0]["tracking_code"] == "VN123456"
    assert refreshed.json()[0]["fulfillment_status"] == "shipped"
