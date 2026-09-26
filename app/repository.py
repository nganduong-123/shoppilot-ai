from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.database import (
    db_session,
    is_integrity_error,
    is_unique_violation,
    json_dumps,
    json_loads,
    row_to_dict,
    utc_now,
)


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


def _json_contains_exact(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_json_contains_exact(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_exact(item, needle) for item in value)
    return str(value) == needle if value is not None else False


BUYING_SIGNALS = (
    "mua", "chốt", "đặt", "lấy", "còn hàng", "còn size", "giá bao nhiêu",
    "phí ship", "giao hàng", "thanh toán", "tư vấn",
)


def _inbox_attention(item: dict[str, Any]) -> dict[str, Any]:
    """Derive an explainable sales-priority snapshot from persisted inbox state."""
    if item["status"] == "resolved":
        return {
            "priority": "resolved",
            "needs_attention": False,
            "sales_intent": False,
            "wait_seconds": 0,
            "sla_breached": False,
        }

    inbound_text = (item.get("last_inbound_message") or "").casefold()
    sales_intent = any(signal in inbound_text for signal in BUYING_SIGNALS)
    waiting_for_human = item["status"] == "waiting" or (
        not item["bot_enabled"] and item.get("last_direction") == "inbound"
    )
    wait_seconds = 0
    if waiting_for_human and item.get("last_inbound_at"):
        try:
            started = datetime.fromisoformat(item["last_inbound_at"])
            wait_seconds = max(0, int((datetime.now(UTC) - started).total_seconds()))
        except (TypeError, ValueError):
            wait_seconds = 0

    sla_breached = waiting_for_human and wait_seconds >= 300
    if sla_breached or (waiting_for_human and sales_intent):
        priority = "urgent"
    elif waiting_for_human or sales_intent:
        priority = "high"
    else:
        priority = "normal"
    return {
        "priority": priority,
        "needs_attention": waiting_for_human,
        "sales_intent": sales_intent,
        "wait_seconds": wait_seconds,
        "sla_breached": sla_breached,
    }


def _inbox_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    rank = {"urgent": 0, "high": 1, "normal": 2, "resolved": 3}[item["priority"]]
    raw_time = item.get("last_inbound_at") or item["last_message_at"]
    try:
        timestamp = datetime.fromisoformat(raw_time).timestamp()
    except (TypeError, ValueError):
        timestamp = 0.0
    # The oldest waiting customer comes first; other queues retain newest-first behavior.
    return rank, timestamp if item["needs_attention"] else -timestamp


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

    def upsert_channel_connection(
        self,
        shop_id: int,
        channel: str,
        external_account_id: str,
        display_name: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO channel_connections
                    (shop_id, channel, external_account_id, display_name, config_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shop_id, channel, external_account_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    config_json = excluded.config_json,
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                (
                    shop_id, channel, external_account_id, display_name,
                    json_dumps(config or {}), now, now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM channel_connections
                WHERE shop_id = ? AND channel = ? AND external_account_id = ?
                """,
                (shop_id, channel, external_account_id),
            ).fetchone()
            result = dict(row)
            result["config"] = json_loads(result.pop("config_json"))
            return result

    def record_channel_event(
        self,
        shop_id: int,
        channel: str,
        external_event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        try:
            with db_session() as connection:
                connection.execute(
                    """
                    INSERT INTO channel_events
                        (shop_id, channel, external_event_id, event_type, payload_json,
                         status, received_at)
                    VALUES (?, ?, ?, ?, ?, 'received', ?)
                    """,
                    (
                        shop_id, channel, external_event_id, event_type,
                        json_dumps(payload), utc_now(),
                    ),
                )
            return True
        except Exception as exc:
            # A repeated platform webhook must not create a second reply.
            if is_unique_violation(exc):
                return False
            if not is_integrity_error(exc):
                raise
            raise

    def mark_channel_event(
        self, channel: str, external_event_id: str, status: str, error: str | None = None
    ) -> None:
        with db_session() as connection:
            connection.execute(
                """
                UPDATE channel_events
                SET status = ?, error_text = ?, processed_at = ?
                WHERE channel = ? AND external_event_id = ?
                """,
                (status, error, utc_now(), channel, external_event_id),
            )

    def get_or_create_channel_conversation(
        self,
        *,
        shop_id: int,
        connection_id: int | None,
        channel: str,
        external_conversation_id: str,
        external_customer_id: str,
        customer_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT * FROM channel_conversations
                WHERE shop_id = ? AND channel = ? AND external_conversation_id = ?
                """,
                (shop_id, channel, external_conversation_id),
            ).fetchone()
            if not row:
                internal_id = str(uuid4())
                channel_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO conversations
                        (id, shop_id, customer_name, status, context_json, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', '{}', ?, ?)
                    """,
                    (internal_id, shop_id, customer_name, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO channel_conversations
                        (id, shop_id, connection_id, channel, external_conversation_id,
                         external_customer_id, customer_name, internal_conversation_id,
                         metadata_json, last_message_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        channel_id, shop_id, connection_id, channel,
                        external_conversation_id, external_customer_id, customer_name,
                        internal_id, json_dumps(metadata or {}), now, now, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM channel_conversations WHERE id = ?", (channel_id,)
                ).fetchone()
            elif row["status"] == "resolved":
                connection.execute(
                    """
                    UPDATE channel_conversations
                    SET status = 'open', updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM channel_conversations WHERE id = ?", (row["id"],)
                ).fetchone()
            result = dict(row)
            result["bot_enabled"] = bool(result["bot_enabled"])
            result["metadata"] = json_loads(result.pop("metadata_json"))
            return result

    def add_channel_message(
        self,
        channel_conversation_id: str,
        *,
        external_message_id: str | None,
        direction: str,
        sender_type: str,
        content: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO channel_messages
                    (channel_conversation_id, external_message_id, direction, sender_type,
                     content, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_conversation_id, external_message_id, direction, sender_type,
                    content, status, json_dumps(metadata or {}), now,
                ),
            )
            connection.execute(
                """
                UPDATE channel_conversations
                SET last_message_at = ?, updated_at = ? WHERE id = ?
                """,
                (now, now, channel_conversation_id),
            )
            row = connection.execute(
                "SELECT * FROM channel_messages WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            result = dict(row)
            result["metadata"] = json_loads(result.pop("metadata_json"))
            return result

    def list_channel_conversations(self, shop_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT cc.*,
                       (SELECT content FROM channel_messages cm
                        WHERE cm.channel_conversation_id = cc.id
                        ORDER BY cm.id DESC LIMIT 1) AS last_message,
                       (SELECT COUNT(*) FROM channel_messages cm
                        WHERE cm.channel_conversation_id = cc.id
                          AND cm.direction = 'inbound' AND cm.status = 'received') AS unread_count
                       ,(SELECT direction FROM channel_messages cm
                         WHERE cm.channel_conversation_id = cc.id
                         ORDER BY cm.id DESC LIMIT 1) AS last_direction
                       ,(SELECT content FROM channel_messages cm
                         WHERE cm.channel_conversation_id = cc.id
                           AND cm.direction = 'inbound'
                         ORDER BY cm.id DESC LIMIT 1) AS last_inbound_message
                       ,(SELECT created_at FROM channel_messages cm
                         WHERE cm.channel_conversation_id = cc.id
                           AND cm.direction = 'inbound'
                         ORDER BY cm.id DESC LIMIT 1) AS last_inbound_at
                FROM channel_conversations cc
                WHERE cc.shop_id = ?
                ORDER BY
                    CASE cc.status WHEN 'waiting' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
                    cc.last_message_at DESC LIMIT ?
                """,
                (shop_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["bot_enabled"] = bool(item["bot_enabled"])
                item["metadata"] = json_loads(item.pop("metadata_json"))
                item.update(_inbox_attention(item))
                result.append(item)
            result.sort(key=_inbox_sort_key)
            return result

    def get_channel_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM channel_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["bot_enabled"] = bool(result["bot_enabled"])
            result["metadata"] = json_loads(result.pop("metadata_json"))
            return result

    def get_channel_conversation_by_external(
        self, shop_id: int, channel: str, external_conversation_id: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT * FROM channel_conversations
                WHERE shop_id = ? AND channel = ? AND external_conversation_id = ?
                """,
                (shop_id, channel, external_conversation_id),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["bot_enabled"] = bool(result["bot_enabled"])
            result["metadata"] = json_loads(result.pop("metadata_json"))
            return result

    def list_channel_messages(self, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM channel_messages WHERE channel_conversation_id = ?
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

    def set_channel_bot(
        self, conversation_id: str, enabled: bool, assigned_to: str | None
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            connection.execute(
                """
                UPDATE channel_conversations
                SET bot_enabled = ?, assigned_to = ?,
                    status = ?, updated_at = ? WHERE id = ?
                """,
                (
                    int(enabled), assigned_to,
                    "open" if enabled or assigned_to else "waiting",
                    utc_now(), conversation_id,
                ),
            )
        return self.get_channel_conversation(conversation_id)

    def update_channel_workflow(
        self,
        conversation_id: str,
        *,
        status: str,
        assigned_to: str | None,
        bot_enabled: bool,
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            connection.execute(
                """
                UPDATE channel_conversations
                SET status = ?, assigned_to = ?, bot_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, assigned_to, int(bot_enabled), utc_now(), conversation_id),
            )
        return self.get_channel_conversation(conversation_id)

    def mark_channel_messages_read(self, conversation_id: str) -> int:
        with db_session() as connection:
            cursor = connection.execute(
                """
                UPDATE channel_messages SET status = 'read'
                WHERE channel_conversation_id = ?
                  AND direction = 'inbound' AND status = 'received'
                """,
                (conversation_id,),
            )
            return cursor.rowcount

    def create_copilot_suggestion(
        self,
        conversation_id: str,
        *,
        summary: str,
        suggested_reply: str,
        model: str,
        risk_flags: list[str],
    ) -> dict[str, Any]:
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO copilot_suggestions
                    (channel_conversation_id, summary, suggested_reply, model,
                     risk_flags_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'generated', ?)
                """,
                (
                    conversation_id, summary, suggested_reply, model,
                    json_dumps(risk_flags), utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM copilot_suggestions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            result = dict(row)
            result["risk_flags"] = json_loads(result.pop("risk_flags_json"), [])
            return result

    def mark_copilot_suggestion_used(
        self, suggestion_id: int, conversation_id: str
    ) -> bool:
        with db_session() as connection:
            cursor = connection.execute(
                """
                UPDATE copilot_suggestions
                SET status = 'used', used_at = ?
                WHERE id = ? AND channel_conversation_id = ? AND status = 'generated'
                """,
                (utc_now(), suggestion_id, conversation_id),
            )
            return cursor.rowcount == 1

    def delete_external_customer_data(
        self,
        *,
        channel: str,
        external_customer_id: str,
        confirmation_code: str,
    ) -> dict[str, Any]:
        """Delete channel data for one platform user and keep an anonymous audit receipt."""
        now = utc_now()
        external_user_hash = hashlib.sha256(
            f"{channel}:{external_customer_id}".encode("utf-8")
        ).hexdigest()
        deleted_records = 0

        with db_session() as connection:
            channel_rows = connection.execute(
                """
                SELECT id, internal_conversation_id
                FROM channel_conversations
                WHERE channel = ? AND external_customer_id = ?
                """,
                (channel, external_customer_id),
            ).fetchall()
            channel_ids = [row["id"] for row in channel_rows]
            conversation_ids = list(
                dict.fromkeys(row["internal_conversation_id"] for row in channel_rows)
            )

            if channel_ids:
                placeholders = ",".join("?" for _ in channel_ids)
                deleted_records += connection.execute(
                    f"SELECT COUNT(*) FROM copilot_suggestions "
                    f"WHERE channel_conversation_id IN ({placeholders})",
                    channel_ids,
                ).fetchone()[0]
                deleted_records += connection.execute(
                    f"SELECT COUNT(*) FROM channel_messages "
                    f"WHERE channel_conversation_id IN ({placeholders})",
                    channel_ids,
                ).fetchone()[0]
                deleted_records += len(channel_ids)

            if conversation_ids:
                placeholders = ",".join("?" for _ in conversation_ids)
                for table in ("messages", "tool_calls", "draft_orders", "handoffs"):
                    deleted_records += connection.execute(
                        f"SELECT COUNT(*) FROM {table} "
                        f"WHERE conversation_id IN ({placeholders})",
                        conversation_ids,
                    ).fetchone()[0]
                deleted_records += len(conversation_ids)

                connection.execute(
                    f"DELETE FROM draft_orders WHERE conversation_id IN ({placeholders})",
                    conversation_ids,
                )
                connection.execute(
                    f"DELETE FROM handoffs WHERE conversation_id IN ({placeholders})",
                    conversation_ids,
                )
                # messages, tool calls, channel conversations and channel messages cascade.
                connection.execute(
                    f"DELETE FROM conversations WHERE id IN ({placeholders})",
                    conversation_ids,
                )

            event_rows = connection.execute(
                "SELECT id, payload_json FROM channel_events WHERE channel = ?",
                (channel,),
            ).fetchall()
            event_ids = [
                row["id"]
                for row in event_rows
                if _json_contains_exact(json_loads(row["payload_json"]), external_customer_id)
            ]
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                connection.execute(
                    f"DELETE FROM channel_events WHERE id IN ({placeholders})", event_ids
                )
                deleted_records += len(event_ids)

            connection.execute(
                """
                INSERT INTO data_deletion_requests
                    (confirmation_code, platform, external_user_hash, deleted_records,
                     status, requested_at, completed_at)
                VALUES (?, ?, ?, ?, 'completed', ?, ?)
                """,
                (
                    confirmation_code,
                    channel,
                    external_user_hash,
                    deleted_records,
                    now,
                    now,
                ),
            )

        return {
            "confirmation_code": confirmation_code,
            "status": "completed",
            "deleted_records": deleted_records,
            "requested_at": now,
            "completed_at": now,
        }

    def get_data_deletion_status(self, confirmation_code: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT confirmation_code, platform, deleted_records, status,
                       requested_at, completed_at
                FROM data_deletion_requests
                WHERE confirmation_code = ?
                """,
                (confirmation_code,),
            ).fetchone()
            return row_to_dict(row)


repository = Repository()
