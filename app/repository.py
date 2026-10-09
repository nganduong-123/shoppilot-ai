from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
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
    def create_user_with_shop(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        shop: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = str(uuid4())
        now = utc_now()
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO users
                    (id, email, display_name, password_hash, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (user_id, email, display_name, password_hash, now),
            )
            existing_shop = connection.execute(
                "SELECT id FROM shops WHERE slug = ?",
                (shop["slug"],),
            ).fetchone()
            if existing_shop:
                member = connection.execute(
                    "SELECT 1 FROM shop_members WHERE shop_id = ? LIMIT 1",
                    (existing_shop["id"],),
                ).fetchone()
                if member:
                    raise ValueError("Shop slug is already owned")
                shop_id = existing_shop["id"]
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO shops
                        (slug, name, category, tagline, policy_text, voice, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shop["slug"], shop["name"], shop["category"], shop["tagline"],
                        shop["policy_text"], shop["voice"], now,
                    ),
                )
                shop_id = cursor.lastrowid
            connection.execute(
                """
                INSERT INTO shop_members (shop_id, user_id, role, created_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (shop_id, user_id, now),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            return row_to_dict(row)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return row_to_dict(row)

    def is_email_verified(self, user_id: str) -> bool:
        with db_session() as connection:
            row = connection.execute(
                "SELECT 1 FROM user_email_status WHERE user_id = ?", (user_id,)
            ).fetchone()
            return bool(row)

    def mark_email_verified(self, user_id: str) -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO user_email_status (user_id, verified_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET verified_at = excluded.verified_at
                """,
                (user_id, utc_now()),
            )

    def create_account_token(
        self, user_id: str, purpose: str, token_hash: str, expires_at: str
    ) -> None:
        with db_session() as connection:
            connection.execute(
                """
                DELETE FROM account_tokens
                WHERE user_id = ? AND purpose = ? AND used_at IS NULL
                """,
                (user_id, purpose),
            )
            connection.execute(
                """
                INSERT INTO account_tokens
                    (id, user_id, purpose, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, purpose, token_hash, expires_at, utc_now()),
            )

    def consume_account_token(
        self, token_hash: str, purpose: str, now: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT t.id, t.user_id, u.email, u.display_name
                FROM account_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = ? AND t.purpose = ?
                  AND t.used_at IS NULL AND t.expires_at > ?
                """,
                (token_hash, purpose, now),
            ).fetchone()
            if not row:
                return None
            updated = connection.execute(
                """
                UPDATE account_tokens SET used_at = ?
                WHERE id = ? AND used_at IS NULL
                """,
                (now, row["id"]),
            )
            return dict(row) if updated.rowcount == 1 else None

    def update_user_password(self, user_id: str, password_hash: str) -> None:
        with db_session() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    def create_auth_session(self, user_id: str, token_hash: str, expires_at: str) -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (id, user_id, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, token_hash, expires_at, utc_now()),
            )

    def get_user_by_session(self, token_hash: str, now: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT u.id, u.email, u.display_name, u.status, u.created_at
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'
                """,
                (token_hash, now),
            ).fetchone()
            return row_to_dict(row)

    def delete_auth_session(self, token_hash: str) -> None:
        with db_session() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def list_user_shops(self, user_id: str) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT s.*, sm.role, COUNT(p.id) AS product_count,
                       COALESCE(SUM(p.stock), 0) AS total_stock
                FROM shop_members sm
                JOIN shops s ON s.id = sm.shop_id
                LEFT JOIN products p ON p.shop_id = s.id AND p.active = 1
                WHERE sm.user_id = ?
                GROUP BY s.id, sm.role
                ORDER BY s.id
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_shop_member(self, shop_id: int, user_id: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT shop_id, user_id, role, created_at
                FROM shop_members WHERE shop_id = ? AND user_id = ?
                """,
                (shop_id, user_id),
            ).fetchone()
            return row_to_dict(row)

    def add_shop_member(self, shop_id: int, user_id: str, role: str = "owner") -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO shop_members (shop_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(shop_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (shop_id, user_id, role, utc_now()),
            )

    def list_shop_team(self, shop_id: int) -> dict[str, list[dict[str, Any]]]:
        now = utc_now()
        with db_session() as connection:
            members = connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.display_name, u.status,
                       sm.role, sm.created_at
                FROM shop_members sm
                JOIN users u ON u.id = sm.user_id
                WHERE sm.shop_id = ?
                ORDER BY CASE sm.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,
                         u.display_name
                """,
                (shop_id,),
            ).fetchall()
            invitations = connection.execute(
                """
                SELECT id, email, role, expires_at, created_at
                FROM shop_invitations
                WHERE shop_id = ? AND accepted_at IS NULL AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (shop_id, now),
            ).fetchall()
        return {
            "members": [dict(row) for row in members],
            "invitations": [dict(row) for row in invitations],
        }

    def create_shop_invitation(
        self,
        *,
        shop_id: int,
        email: str,
        role: str,
        token_hash: str,
        expires_at: str,
        invited_by: str,
    ) -> dict[str, Any]:
        invitation_id = str(uuid4())
        now = utc_now()
        with db_session() as connection:
            existing_member = connection.execute(
                """
                SELECT 1 FROM shop_members sm
                JOIN users u ON u.id = sm.user_id
                WHERE sm.shop_id = ? AND u.email = ?
                """,
                (shop_id, email),
            ).fetchone()
            if existing_member:
                raise ValueError("User is already a member")
            connection.execute(
                """
                DELETE FROM shop_invitations
                WHERE shop_id = ? AND email = ? AND accepted_at IS NULL
                """,
                (shop_id, email),
            )
            connection.execute(
                """
                INSERT INTO shop_invitations
                    (id, shop_id, email, role, token_hash, expires_at,
                     invited_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invitation_id,
                    shop_id,
                    email,
                    role,
                    token_hash,
                    expires_at,
                    invited_by,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, email, role, expires_at, created_at
                FROM shop_invitations WHERE id = ?
                """,
                (invitation_id,),
            ).fetchone()
            return dict(row)

    def accept_shop_invitation(
        self, token_hash: str, user_id: str, now: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            invitation = connection.execute(
                """
                SELECT * FROM shop_invitations
                WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            user = connection.execute(
                "SELECT email FROM users WHERE id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
            if not invitation or not user or user["email"] != invitation["email"]:
                return None
            updated = connection.execute(
                """
                UPDATE shop_invitations SET accepted_at = ?
                WHERE id = ? AND accepted_at IS NULL
                """,
                (now, invitation["id"]),
            )
            if updated.rowcount != 1:
                return None
            connection.execute(
                """
                INSERT INTO shop_members (shop_id, user_id, role, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(shop_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (invitation["shop_id"], user_id, invitation["role"], now),
            )
            shop = connection.execute(
                "SELECT slug, name FROM shops WHERE id = ?", (invitation["shop_id"],)
            ).fetchone()
            return {**dict(shop), "role": invitation["role"]}

    def revoke_shop_invitation(self, shop_id: int, invitation_id: str) -> bool:
        with db_session() as connection:
            cursor = connection.execute(
                """
                DELETE FROM shop_invitations
                WHERE id = ? AND shop_id = ? AND accepted_at IS NULL
                """,
                (invitation_id, shop_id),
            )
            return cursor.rowcount == 1

    def update_shop_member_role(
        self, shop_id: int, user_id: str, role: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            member = connection.execute(
                "SELECT role FROM shop_members WHERE shop_id = ? AND user_id = ?",
                (shop_id, user_id),
            ).fetchone()
            if not member:
                return None
            if member["role"] == "owner" and role != "owner":
                owners = connection.execute(
                    "SELECT COUNT(*) FROM shop_members WHERE shop_id = ? AND role = 'owner'",
                    (shop_id,),
                ).fetchone()[0]
                if owners <= 1:
                    raise ValueError("Shop must keep at least one owner")
            connection.execute(
                "UPDATE shop_members SET role = ? WHERE shop_id = ? AND user_id = ?",
                (role, shop_id, user_id),
            )
            row = connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.display_name, u.status,
                       sm.role, sm.created_at
                FROM shop_members sm JOIN users u ON u.id = sm.user_id
                WHERE sm.shop_id = ? AND sm.user_id = ?
                """,
                (shop_id, user_id),
            ).fetchone()
            return dict(row)

    def remove_shop_member(self, shop_id: int, user_id: str) -> bool:
        with db_session() as connection:
            member = connection.execute(
                "SELECT role FROM shop_members WHERE shop_id = ? AND user_id = ?",
                (shop_id, user_id),
            ).fetchone()
            if not member:
                return False
            if member["role"] == "owner":
                owners = connection.execute(
                    "SELECT COUNT(*) FROM shop_members WHERE shop_id = ? AND role = 'owner'",
                    (shop_id,),
                ).fetchone()[0]
                if owners <= 1:
                    raise ValueError("Shop must keep at least one owner")
            cursor = connection.execute(
                "DELETE FROM shop_members WHERE shop_id = ? AND user_id = ?",
                (shop_id, user_id),
            )
            return cursor.rowcount == 1

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

    def get_shop_by_id(self, shop_id: int) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
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

    def update_shop(self, shop_id: int, data: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "category", "tagline", "policy_text", "voice"}
        updates = {key: value for key, value in data.items() if key in allowed and value is not None}
        if not updates:
            shop = self.get_shop_by_id(shop_id)
            if not shop:
                raise ValueError("Shop not found")
            return shop
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with db_session() as connection:
            connection.execute(
                f"UPDATE shops SET {assignments} WHERE id = ?",
                (*updates.values(), shop_id),
            )
            row = connection.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
            if not row:
                raise ValueError("Shop not found")
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

    def list_orders(self, shop_id: int) -> list[dict[str, Any]]:
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT d.*,
                       COALESCE(f.status,
                           CASE WHEN d.status = 'confirmed' THEN 'processing'
                                ELSE d.status END) AS fulfillment_status,
                       f.tracking_code, f.note AS fulfillment_note,
                       f.updated_at AS fulfillment_updated_at
                FROM draft_orders d
                LEFT JOIN order_fulfillment f ON f.order_id = d.id
                WHERE d.shop_id = ?
                ORDER BY d.created_at DESC
                """,
                (shop_id,),
            ).fetchall()
        orders = []
        for row in rows:
            order = dict(row)
            order["customer"] = json_loads(order.pop("customer_json"), {})
            order["items"] = json_loads(order.pop("items_json"), [])
            orders.append(order)
        return orders

    def update_order_fulfillment(
        self,
        *,
        shop_id: int,
        order_id: str,
        status: str,
        tracking_code: str | None,
        note: str | None,
        updated_by: str,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with db_session() as connection:
            order = connection.execute(
                """
                SELECT id FROM draft_orders
                WHERE id = ? AND shop_id = ? AND status = 'confirmed'
                """,
                (order_id, shop_id),
            ).fetchone()
            if not order:
                return None
            connection.execute(
                """
                INSERT INTO order_fulfillment
                    (order_id, status, tracking_code, note, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status = excluded.status,
                    tracking_code = excluded.tracking_code,
                    note = excluded.note,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (order_id, status, tracking_code, note, updated_by, now),
            )
            row = connection.execute(
                "SELECT * FROM order_fulfillment WHERE order_id = ?", (order_id,)
            ).fetchone()
            return dict(row)

    def get_shop_subscription(self, shop_id: int) -> dict[str, Any]:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)
            ).fetchone()
        if row:
            return dict(row)
        return {
            "shop_id": shop_id,
            "plan": "free",
            "status": "active",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "current_period_end": None,
            "updated_at": None,
        }

    def get_subscription_by_provider_id(self, subscription_id: str) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT * FROM shop_subscriptions
                WHERE stripe_subscription_id = ?
                """,
                (subscription_id,),
            ).fetchone()
            return row_to_dict(row)

    def upsert_shop_subscription(
        self,
        *,
        shop_id: int,
        plan: str,
        status: str,
        customer_id: str | None,
        subscription_id: str | None,
        current_period_end: str | None = None,
    ) -> dict[str, Any]:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO shop_subscriptions
                    (shop_id, plan, status, stripe_customer_id,
                     stripe_subscription_id, current_period_end, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shop_id) DO UPDATE SET
                    plan = excluded.plan,
                    status = excluded.status,
                    stripe_customer_id = COALESCE(excluded.stripe_customer_id,
                                                  shop_subscriptions.stripe_customer_id),
                    stripe_subscription_id = COALESCE(excluded.stripe_subscription_id,
                                                      shop_subscriptions.stripe_subscription_id),
                    current_period_end = excluded.current_period_end,
                    updated_at = excluded.updated_at
                """,
                (
                    shop_id,
                    plan,
                    status,
                    customer_id,
                    subscription_id,
                    current_period_end,
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)
            ).fetchone()
            return dict(row)

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
            feedback = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN f.label = 'helpful' THEN 1 ELSE 0 END) AS helpful
                FROM conversation_feedback f
                JOIN conversations c ON c.id = f.conversation_id
                WHERE c.shop_id = ?
                """,
                (shop_id,),
            ).fetchone()
        data = dict(row)
        data["product_count"] = product_count
        conversations = data["conversations"] or 0
        data["automation_rate"] = (
            round(100 * (conversations - data["handoffs"]) / conversations, 1)
            if conversations
            else 100.0
        )
        data["feedback_total"] = feedback["total"] or 0
        data["feedback_helpful"] = feedback["helpful"] or 0
        data["feedback_helpful_rate"] = (
            round(100 * data["feedback_helpful"] / data["feedback_total"], 1)
            if data["feedback_total"]
            else None
        )
        return data

    def add_conversation_feedback(
        self,
        conversation_id: str,
        label: str,
        note: str | None,
        evaluator_id: str | None,
    ) -> dict[str, Any]:
        with db_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversation_feedback
                    (conversation_id, label, note, evaluator_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, label, note, evaluator_id, utc_now()),
            )
            row = connection.execute(
                "SELECT * FROM conversation_feedback WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

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

    def get_channel_connection(self, connection_id: int) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                "SELECT * FROM channel_connections WHERE id = ?", (connection_id,)
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["config"] = json_loads(result.pop("config_json"))
            return result

    def get_channel_connection_by_external(
        self, channel: str, external_account_id: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT * FROM channel_connections
                WHERE channel = ? AND external_account_id = ? AND status = 'active'
                """,
                (channel, external_account_id),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["config"] = json_loads(result.pop("config_json"))
            return result

    def delete_environment_channel_connection(
        self,
        connection_id: int,
        *,
        legacy_page_id: str | None = None,
    ) -> bool:
        """Remove a current or legacy env-seeded Page placeholder."""
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT shop_id, channel, external_account_id, config_json
                FROM channel_connections WHERE id = ?
                """,
                (connection_id,),
            ).fetchone()
            if not row:
                return False
            config = json_loads(row["config_json"])
            is_environment_seed = config.get("source") == "environment"
            is_legacy_environment_seed = bool(
                not config.get("page_access_token_enc")
                and legacy_page_id
                and row["channel"] == "messenger"
                and row["external_account_id"] == legacy_page_id
            )
            if not (is_environment_seed or is_legacy_environment_seed):
                return False
            return bool(
                connection.execute(
                    "DELETE FROM channel_connections WHERE id = ?",
                    (connection_id,),
                ).rowcount
            )

    def list_channel_connections(
        self, shop_id: int, channel: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM channel_connections WHERE shop_id = ?"
        params: list[Any] = [shop_id]
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        query += " ORDER BY id"
        with db_session() as connection:
            rows = connection.execute(query, params).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["config"] = json_loads(item.pop("config_json"))
                result.append(item)
            return result

    def create_meta_oauth_state(
        self, state_hash: str, user_id: str, shop_id: int, expires_at: str
    ) -> None:
        with db_session() as connection:
            connection.execute(
                """
                INSERT INTO meta_oauth_states
                    (state_hash, user_id, shop_id, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (state_hash, user_id, shop_id, expires_at, utc_now()),
            )

    def set_meta_oauth_candidates(self, state_hash: str, candidates: list[dict[str, Any]]) -> None:
        with db_session() as connection:
            connection.execute(
                """
                UPDATE meta_oauth_states SET candidates_json = ?
                WHERE state_hash = ? AND used_at IS NULL
                """,
                (json_dumps(candidates), state_hash),
            )

    def get_meta_oauth_state(
        self, state_hash: str, user_id: str, now: str
    ) -> dict[str, Any] | None:
        with db_session() as connection:
            row = connection.execute(
                """
                SELECT * FROM meta_oauth_states
                WHERE state_hash = ? AND user_id = ? AND expires_at > ? AND used_at IS NULL
                """,
                (state_hash, user_id, now),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["candidates"] = json_loads(result.pop("candidates_json"), [])
            return result

    def consume_meta_oauth_state(self, state_hash: str) -> None:
        with db_session() as connection:
            connection.execute(
                "UPDATE meta_oauth_states SET used_at = ? WHERE state_hash = ?",
                (utc_now(), state_hash),
            )

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
            f"{channel}:{external_customer_id}".encode()
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
                        f"SELECT COUNT(*) FROM {table} WHERE conversation_id IN ({placeholders})",
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

    def enqueue_job(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str | None = None,
        max_attempts: int = 5,
        connection: Any | None = None,
    ) -> bool:
        now = utc_now()
        values = (
            str(uuid4()), kind, dedupe_key, json_dumps(payload),
            max_attempts, now, now, now,
        )

        def insert(target: Any) -> bool:
            cursor = target.execute(
                """
                INSERT INTO jobs
                    (id, kind, dedupe_key, payload_json, status, attempts,
                     max_attempts, available_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                values,
            )
            return cursor.rowcount == 1

        if connection is not None:
            return insert(connection)
        with db_session() as managed_connection:
            return insert(managed_connection)

    def requeue_stale_jobs(self, stale_before: str) -> int:
        with db_session() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'queued', locked_at = NULL,
                    available_at = ?, updated_at = ?
                WHERE status = 'running' AND locked_at < ?
                """,
                (utc_now(), utc_now(), stale_before),
            )
            return cursor.rowcount

    def claim_job(self) -> dict[str, Any] | None:
        now = utc_now()
        with db_session() as connection:
            candidates = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued' AND available_at <= ?
                ORDER BY created_at LIMIT 5
                """,
                (now,),
            ).fetchall()
            for candidate in candidates:
                cursor = connection.execute(
                    """
                    UPDATE jobs SET status = 'running', attempts = attempts + 1,
                        locked_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (now, now, candidate["id"]),
                )
                if cursor.rowcount != 1:
                    continue
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (candidate["id"],)
                ).fetchone()
                result = dict(row)
                result["payload"] = json_loads(result.pop("payload_json"))
                return result
        return None

    def complete_job(self, job_id: str) -> None:
        with db_session() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = 'completed', locked_at = NULL,
                    last_error = NULL, updated_at = ? WHERE id = ?
                """,
                (utc_now(), job_id),
            )

    def fail_job(self, job_id: str, error: str, attempts: int, max_attempts: int) -> None:
        terminal = attempts >= max_attempts
        delay_seconds = min(300, 2 ** max(1, attempts))
        available_at = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()
        with db_session() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = ?, locked_at = NULL, last_error = ?,
                    available_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    "failed" if terminal else "queued",
                    error[:1000], available_at, utc_now(), job_id,
                ),
            )

    def job_stats(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "failed": 0}
        with db_session() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS total FROM jobs
                WHERE status IN ('queued', 'running', 'failed') GROUP BY status
                """
            ).fetchall()
            for row in rows:
                counts[row["status"]] = row["total"]
        return counts


repository = Repository()
