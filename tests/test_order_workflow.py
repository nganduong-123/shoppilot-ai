from app.repository import repository
from app.tools import ShopTools, confirm_pending_order


def test_order_is_only_a_draft_until_explicit_confirmation():
    shop = repository.get_shop("mint-fashion")
    repository.create_conversation("safe-order", shop["id"])
    tools = ShopTools(shop, "safe-order")
    product = repository.list_products(shop["id"])[0]
    stock_before = product["stock"]

    draft = tools.execute(
        "prepare_draft_order",
        {"product_id": product["id"], "quantity": 2, "location": "Quận 3"},
    )

    assert draft["created"] is True
    assert draft["status"] == "awaiting_confirmation"
    assert draft["confirmation_required"] is True
    assert repository.get_product(shop["id"], product["id"])["stock"] == stock_before

    confirmed = confirm_pending_order("safe-order")

    assert confirmed["confirmed"] is True
    assert confirmed["status"] == "confirmed"
    assert repository.get_product(shop["id"], product["id"])["stock"] == stock_before - 2
    assert repository.get_conversation("safe-order")["pending_action"] is None


def test_cannot_create_order_above_stock():
    shop = repository.get_shop("mint-fashion")
    repository.create_conversation("stock-guard", shop["id"])
    tools = ShopTools(shop, "stock-guard")
    product = repository.list_products(shop["id"])[0]

    result = tools.execute(
        "prepare_draft_order",
        {"product_id": product["id"], "quantity": product["stock"] + 1},
    )

    assert result["created"] is False
    assert result["available"] == product["stock"]
    assert repository.get_conversation("stock-guard")["pending_action"] is None
