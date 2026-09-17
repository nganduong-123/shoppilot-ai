from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Callable
from uuid import uuid4

from app.database import db_session, json_dumps, json_loads, utc_now
from app.repository import Repository, repository


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", value).strip()


def money(value: int) -> str:
    return f"{value:,}đ".replace(",", ".")


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Tìm sản phẩm trong đúng catalog của shop theo nhu cầu khách. Gọi tool này trước khi tư vấn sản phẩm; không tự bịa sản phẩm, giá hoặc tồn kho.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên, loại hoặc mô tả sản phẩm"},
                    "max_price": {"type": ["integer", "null"], "description": "Ngân sách tối đa VND"},
                    "color": {"type": ["string", "null"]},
                    "size": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_inventory",
            "description": "Kiểm tra tồn kho hiện tại của một sản phẩm bằng product_id.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_shipping",
            "description": "Tính phí và thời gian giao hàng theo khu vực và giá trị đơn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "order_value": {"type": "integer", "minimum": 0},
                },
                "required": ["location", "order_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_policy",
            "description": "Tra cứu chính sách giao hàng, đổi trả, bảo hành hoặc phạm vi tư vấn của shop.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_draft_order",
            "description": "Tạo đơn NHÁP khi khách thể hiện rõ muốn mua một sản phẩm. Việc này chưa đặt hàng; khách vẫn phải xác nhận ở lượt sau.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 20},
                    "location": {"type": "string"},
                },
                "required": ["product_id", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_human",
            "description": "Chuyển cho nhân viên khi khách yêu cầu, đang tức giận, cần ngoại lệ/giảm giá hoặc agent không đủ căn cứ.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


class ShopTools:
    def __init__(
        self,
        shop: dict[str, Any],
        conversation_id: str,
        repo: Repository = repository,
    ) -> None:
        self.shop = shop
        self.conversation_id = conversation_id
        self.repo = repo

    def search_products(
        self,
        query: str,
        max_price: int | None = None,
        color: str | None = None,
        size: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        products = self.repo.list_products(self.shop["id"])
        query_tokens = set(normalize_text(query).split())
        color_norm = normalize_text(color or "")
        if not color_norm:
            known_colors = ["trang", "den", "xanh", "be", "xam", "reu", "hong", "do"]
            color_norm = next((item for item in known_colors if item in query_tokens), "")
        size_norm = normalize_text(size or "")
        ranked: list[tuple[int, dict[str, Any]]] = []

        generic_words = {
            "san", "pham", "tim", "muon", "can", "goi", "y", "tu", "van",
            "toi", "minh", "cho", "duoi", "tren", "khoang", "size", "co",
        }
        query_tokens = {
            token for token in query_tokens - generic_words
            if not token.isdigit()
            and token not in {"s", "m", "l", "xl", "2xl"}
            and not re.fullmatch(r"\d+(?:k|tr)?", token)
        }
        for product in products:
            if max_price is not None and product["price"] > max_price:
                continue
            searchable = normalize_text(
                " ".join(
                    [
                        product["name"],
                        product["category"],
                        product["description"],
                        str(product["attributes"]),
                    ]
                )
            )
            if color_norm and color_norm not in searchable:
                continue
            if size_norm and size_norm not in searchable:
                continue
            matched_tokens = {token for token in query_tokens if token in searchable}
            if query_tokens:
                coverage = len(matched_tokens) / len(query_tokens)
                minimum_coverage = 0.6 if len(query_tokens) >= 2 else 1.0
                if coverage < minimum_coverage:
                    continue
            score = len(matched_tokens) * 3
            if product["stock"] > 0:
                score += 1
            if not query_tokens:
                score += 1
            if score > 0:
                ranked.append((score, product))

        ranked.sort(key=lambda item: (-item[0], item[1]["price"]))
        matches = [
            {
                "id": product["id"],
                "sku": product["sku"],
                "name": product["name"],
                "category": product["category"],
                "description": product["description"],
                "price": product["price"],
                "price_display": money(product["price"]),
                "stock": product["stock"],
                "in_stock": product["stock"] > 0,
                "attributes": product["attributes"],
            }
            for _, product in ranked[: max(1, min(limit, 5))]
        ]
        context = self.repo.get_conversation(self.conversation_id)["context"]
        context["last_product_ids"] = [product["id"] for product in matches]
        preferences = context.get("preferences", {})
        preferences.update(
            {
                key: value
                for key, value in {
                    "query": query,
                    "max_price": max_price,
                    "color": color or color_norm or None,
                    "size": size,
                }.items()
                if value not in (None, "")
            }
        )
        context["preferences"] = preferences
        self.repo.update_conversation(self.conversation_id, context=context)
        return {"count": len(matches), "products": matches, "source": "shop_catalog"}

    def check_inventory(self, product_id: int) -> dict[str, Any]:
        product = self.repo.get_product(self.shop["id"], product_id)
        if not product:
            return {"found": False, "message": "Không tìm thấy sản phẩm trong shop này."}
        return {
            "found": True,
            "product_id": product["id"],
            "name": product["name"],
            "stock": product["stock"],
            "in_stock": product["stock"] > 0,
            "attributes": product["attributes"],
            "source": "inventory_database",
        }

    def calculate_shipping(self, location: str, order_value: int) -> dict[str, Any]:
        location_norm = normalize_text(location)
        is_hcm = any(token in location_norm for token in ["hcm", "ho chi minh", "sai gon", "quan"])
        threshold = 500_000 if self.shop["slug"] == "mint-fashion" else 600_000
        if self.shop["slug"] == "nova-tech":
            threshold = 1_000_000
        fee = 0 if order_value >= threshold else (30_000 if is_hcm else 45_000)
        return {
            "location": location or "Chưa cung cấp",
            "fee": fee,
            "fee_display": money(fee) if fee else "Miễn phí",
            "eta": "1-2 ngày" if is_hcm else "3-5 ngày",
            "free_shipping_threshold": threshold,
            "source": "shipping_rules",
        }

    def retrieve_policy(self, question: str) -> dict[str, Any]:
        sentences = [
            part.strip() + "."
            for part in re.split(r"[.!?]+", self.shop["policy_text"])
            if part.strip()
        ]
        tokens = set(normalize_text(question).split())
        scored = []
        for sentence in sentences:
            score = sum(1 for token in tokens if token in normalize_text(sentence))
            scored.append((score, sentence))
        scored.sort(key=lambda item: -item[0])
        evidence = [sentence for score, sentence in scored[:3] if score > 0]
        if not evidence:
            evidence = sentences[:2]
        return {
            "answer_basis": evidence,
            "source": f"policy:{self.shop['slug']}",
            "instruction": "Chỉ trả lời dựa trên các câu chính sách này.",
        }

    def prepare_draft_order(
        self,
        product_id: int,
        quantity: int = 1,
        location: str = "",
    ) -> dict[str, Any]:
        product = self.repo.get_product(self.shop["id"], product_id)
        if not product:
            return {"created": False, "error": "Không tìm thấy sản phẩm."}
        if product["stock"] < quantity:
            return {
                "created": False,
                "error": "Không đủ tồn kho.",
                "available": product["stock"],
            }
        subtotal = product["price"] * quantity
        shipping = self.calculate_shipping(location, subtotal)
        draft_id = f"DR-{uuid4().hex[:8].upper()}"
        now = utc_now()
        item = {
            "product_id": product["id"],
            "sku": product["sku"],
            "name": product["name"],
            "quantity": quantity,
            "unit_price": product["price"],
        }
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO draft_orders
                    (id, conversation_id, shop_id, items_json, subtotal,
                     shipping_fee, total, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_confirmation', ?, ?)
                """,
                (
                    draft_id,
                    self.conversation_id,
                    self.shop["id"],
                    json_dumps([item]),
                    subtotal,
                    shipping["fee"],
                    subtotal + shipping["fee"],
                    now,
                    now,
                ),
            )
        pending = {"type": "confirm_order", "draft_order_id": draft_id}
        self.repo.update_conversation(self.conversation_id, pending_action=pending)
        return {
            "created": True,
            "draft_order_id": draft_id,
            "status": "awaiting_confirmation",
            "item": item,
            "subtotal": subtotal,
            "shipping_fee": shipping["fee"],
            "total": subtotal + shipping["fee"],
            "total_display": money(subtotal + shipping["fee"]),
            "confirmation_required": True,
        }

    def handoff_to_human(self, reason: str) -> dict[str, Any]:
        messages = self.repo.list_messages(self.conversation_id, 8)
        summary = " | ".join(
            f"{item['role']}: {item['content'][:120]}" for item in messages[-5:]
        )
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO handoffs (conversation_id, reason, summary, status, created_at)
                VALUES (?, ?, ?, 'waiting', ?)
                """,
                (self.conversation_id, reason, summary, utc_now()),
            )
        self.repo.update_conversation(self.conversation_id, status="handoff")
        return {
            "handoff_id": cursor.lastrowid,
            "status": "waiting",
            "reason": reason,
            "message": "Đã chuyển đầy đủ ngữ cảnh cho nhân viên.",
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed: dict[str, Callable[..., dict[str, Any]]] = {
            "search_products": self.search_products,
            "check_inventory": self.check_inventory,
            "calculate_shipping": self.calculate_shipping,
            "retrieve_policy": self.retrieve_policy,
            "prepare_draft_order": self.prepare_draft_order,
            "handoff_to_human": self.handoff_to_human,
        }
        started = time.perf_counter()
        success = True
        try:
            if name not in allowed:
                raise ValueError(f"Tool không được phép: {name}")
            result = allowed[name](**arguments)
        except Exception as exc:
            success = False
            result = {"error": str(exc), "success": False}
        duration = int((time.perf_counter() - started) * 1000)
        self.repo.log_tool_call(
            self.conversation_id, name, arguments, result, duration, success
        )
        return result


def confirm_pending_order(
    conversation_id: str, repo: Repository = repository
) -> dict[str, Any] | None:
    conversation = repo.get_conversation(conversation_id)
    pending = conversation.get("pending_action") if conversation else None
    if not pending or pending.get("type") != "confirm_order":
        return None
    draft_id = pending["draft_order_id"]
    with db_session() as connection:
        row = connection.execute(
            "SELECT * FROM draft_orders WHERE id = ? AND status = 'awaiting_confirmation'",
            (draft_id,),
        ).fetchone()
        if not row:
            return None
        items = json_loads(row["items_json"], [])
        for item in items:
            product = connection.execute(
                "SELECT stock FROM products WHERE id = ?", (item["product_id"],)
            ).fetchone()
            if not product or product["stock"] < item["quantity"]:
                return {"confirmed": False, "error": "Sản phẩm vừa hết hoặc không đủ hàng."}
        for item in items:
            connection.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ?",
                (item["quantity"], item["product_id"]),
            )
        connection.execute(
            "UPDATE draft_orders SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (utc_now(), draft_id),
        )
    repo.update_conversation(conversation_id, clear_pending=True)
    return {
        "confirmed": True,
        "order_id": draft_id.replace("DR-", "SP-"),
        "draft_order_id": draft_id,
        "status": "confirmed",
    }
