from __future__ import annotations

from typing import Any

from app.database import db_session, json_dumps, json_loads, row_to_dict, utc_now


def _hydrate_product(row: Any) -> dict[str, Any]:
    product = dict(row)
    product["attributes"] = json_loads(product.pop("attributes_json"))
    product["active"] = bool(product["active"])
    return product


def _hydrate_conversation(row: Any) -> dict[str, Any]:
    conversation = dict(row)
    conversation["context"] = json_loads(conversation.pop("context_json"))
    pending_raw = conversation.pop("pending_action_json")
    conversation["pending_action"] = json_loads(pending_raw) if pending_raw else None
    return conversation


class Repository:
    def list_shops(self) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT s.*, COUNT(p.id) AS product_count,
                       COALESCE(SUM(p.stock), 0) AS total_stock
                FROM shops s
                LEFT JOIN products p ON p.shop_id = s.id AND p.active = 1
                GROUP BY s.id
                ORDER BY s.id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_shop(self, slug: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute("SELECT * FROM shops WHERE slug = ?", (slug,)).fetchone()
            return row_to_dict(row)

    def create_shop(self, data: dict[str, Any]) -> dict[str, Any]:
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shops
                    (slug, name, category, tagline, policy_text, voice, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["slug"], data["name"], data["category"], data["tagline"],
                    data["policy_text"], data["voice"], utc_now(),
                ),
            )
            row = connection.execute("SELECT * FROM shops WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def list_products(self, shop_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM products WHERE shop_id = ?"
        params: list[Any] = [shop_id]
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY stock DESC, id"
        with db_session() as connection:
            rows = connection.execute(query, params).fetchall()
            return [_hydrate_product(row) for row in rows]

    def get_product(self, shop_id: int, product_id: int) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM products WHERE shop_id = ? AND id = ? AND active = 1",
                (shop_id, product_id),
            ).fetchone()
            return _hydrate_product(row) if row else None

    def create_product(self, shop_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO products
                    (shop_id, sku, name, category, description, price, stock, attributes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shop_id, data["sku"], data["name"], data["category"],
                    data["description"], data["price"], data["stock"],
                    json_dumps(data.get("attributes", {})),
                ),
            )
            row = connection.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return _hydrate_product(row)

    def create_conversation(self, conversation_id: str, shop_id: int) -> dict[str, Any]:
        now = utc_now()
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO conversations
                    (id, shop_id, status, context_json, created_at, updated_at)
                VALUES (?, ?, 'active', '{}', ?, ?)
                """,
                (conversation_id, shop_id, now, now),
            )
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            return _hydrate_conversation(row)

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            return _hydrate_conversation(row) if row else None

    def update_conversation(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
        context: dict[str, Any] | None = None,
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> None:
        fields = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if context is not None:
            fields.append("context_json = ?")
            values.append(json_dumps(context))
        if pending_action is not None:
            fields.append("pending_action_json = ?")
            values.append(json_dumps(pending_action))
        elif clear_pending:
            fields.append("pending_action_json = NULL")
        values.append(conversation_id)
        with db_session() as connection:
            connection.execute(
                f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", values
            )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO messages
                    (conversation_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, json_dumps(metadata or {}), utc_now()),
            )

    def list_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages WHERE conversation_id = ?
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id
                """,
                (conversation_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json_loads(item.pop("metadata_json"))
                result.append(item)
            return result

    def log_tool_call(
        self,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        duration_ms: int,
        success: bool,
    ) -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls
                    (conversation_id, tool_name, arguments_json, result_json,
                     duration_ms, success, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id, tool_name, json_dumps(arguments), json_dumps(result),
                    duration_ms, int(success), utc_now(),
                ),
            )

    def get_trace(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return {}
        with db_session() as connection:
            calls = connection.execute(
                "SELECT * FROM tool_calls WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            orders = connection.execute(
                "SELECT * FROM draft_orders WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
            handoffs = connection.execute(
                "SELECT * FROM handoffs WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return {
            "conversation": conversation,
            "messages": self.list_messages(conversation_id, 100),
            "tool_calls": [
                {
                    **dict(row),
                    "arguments": json_loads(row["arguments_json"]),
                    "result": json_loads(row["result_json"]),
                }
                for row in calls
            ],
            "orders": [
                {
                    **dict(row),
                    "customer": json_loads(row["customer_json"]),
                    "items": json_loads(row["items_json"], []),
                }
                for row in orders
            ],
            "handoffs": [dict(row) for row in handoffs],
        }

    def metrics(self, shop_id: int) -> dict[str, Any]:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM conversations WHERE shop_id = ?) AS conversations,
                    (SELECT COUNT(*) FROM conversations WHERE shop_id = ? AND status = 'handoff') AS handoffs,
                    (SELECT COUNT(*) FROM draft_orders WHERE shop_id = ? AND status = 'confirmed') AS confirmed_orders,
                    (SELECT COALESCE(SUM(total), 0) FROM draft_orders WHERE shop_id = ? AND status = 'confirmed') AS revenue,
                    (SELECT COUNT(*) FROM tool_calls tc
                     JOIN conversations c ON c.id = tc.conversation_id
                     WHERE c.shop_id = ?) AS tool_calls
                """,
                (shop_id, shop_id, shop_id, shop_id, shop_id),
            ).fetchone()
            product_count = connection.execute(
                "SELECT COUNT(*) AS count FROM products WHERE shop_id = ? AND active = 1",
                (shop_id,),
            ).fetchone()["count"]
        data = dict(row)
        data["product_count"] = product_count
        conversations = data["conversations"] or 0
        data["automation_rate"] = round(
            100 * (conversations - data["handoffs"]) / conversations, 1
        ) if conversations else 100.0
        return data


repository = Repository()
