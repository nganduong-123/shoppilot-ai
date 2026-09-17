from app.repository import repository
from app.tools import ShopTools


def test_demo_has_three_isolated_shops():
    shops = repository.list_shops()
    assert [shop["slug"] for shop in shops] == [
        "mint-fashion",
        "lumi-beauty",
        "nova-tech",
    ]
    assert [shop["product_count"] for shop in shops] == [5, 3, 3]


def test_search_never_leaks_products_from_another_shop():
    shop = repository.get_shop("lumi-beauty")
    conversation = repository.create_conversation("tenant-isolation", shop["id"])
    tools = ShopTools(shop, conversation["id"])

    result = tools.execute("search_products", {"query": "tai nghe", "limit": 5})

    assert result["count"] == 0
    assert all(not item["sku"].startswith("NT-") for item in result["products"])


def test_fashion_search_applies_budget_color_and_size():
    shop = repository.get_shop("mint-fashion")
    repository.create_conversation("search-filters", shop["id"])
    tools = ShopTools(shop, "search-filters")

    result = tools.execute(
        "search_products",
        {"query": "áo sơ mi trắng", "max_price": 500_000, "size": "M", "limit": 5},
    )

    assert result["count"] == 1
    assert result["products"][0]["sku"] == "MF-SM01"
