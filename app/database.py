from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import settings

try:
    import psycopg
except ImportError:  # pragma: no cover - installed in production
    psycopg = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    tagline TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'VND',
    primary_color TEXT NOT NULL DEFAULT '#15d9bd',
    accent_color TEXT NOT NULL DEFAULT '#7557ff',
    policy_text TEXT NOT NULL,
    voice TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    price INTEGER NOT NULL CHECK(price >= 0),
    stock INTEGER NOT NULL CHECK(stock >= 0),
    attributes_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(shop_id, sku)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    customer_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    context_json TEXT NOT NULL DEFAULT '{}',
    pending_action_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    success INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_orders (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    customer_json TEXT NOT NULL DEFAULT '{}',
    items_json TEXT NOT NULL,
    subtotal INTEGER NOT NULL,
    shipping_fee INTEGER NOT NULL,
    total INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'awaiting_confirmation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    external_account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(shop_id, channel, external_account_id)
);

CREATE TABLE IF NOT EXISTS channel_conversations (
    id TEXT PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    connection_id INTEGER REFERENCES channel_connections(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    external_conversation_id TEXT NOT NULL,
    external_customer_id TEXT NOT NULL,
    customer_name TEXT,
    internal_conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    bot_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    assigned_to TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_message_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, external_conversation_id)
);

CREATE TABLE IF NOT EXISTS channel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_conversation_id TEXT NOT NULL REFERENCES channel_conversations(id) ON DELETE CASCADE,
    external_message_id TEXT,
    direction TEXT NOT NULL,
    sender_type TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(channel_conversation_id, external_message_id)
);

CREATE TABLE IF NOT EXISTS channel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    error_text TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(channel, external_event_id)
);

CREATE TABLE IF NOT EXISTS copilot_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_conversation_id TEXT NOT NULL REFERENCES channel_conversations(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    suggested_reply TEXT NOT NULL,
    model TEXT NOT NULL,
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'generated',
    created_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS data_deletion_requests (
    confirmation_code TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_user_hash TEXT NOT NULL,
    deleted_records INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    requested_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shop_members (
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'agent',
    created_at TEXT NOT NULL,
    PRIMARY KEY(shop_id, user_id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta_oauth_states (
    state_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    candidates_json TEXT,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_conversation ON tool_calls(conversation_id);
CREATE INDEX IF NOT EXISTS idx_channel_conversations_shop ON channel_conversations(shop_id, last_message_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_connection_account
    ON channel_connections(channel, external_account_id);
CREATE INDEX IF NOT EXISTS idx_channel_messages_conversation ON channel_messages(channel_conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_channel_events_status ON channel_events(status, received_at);
CREATE INDEX IF NOT EXISTS idx_copilot_conversation
    ON copilot_suggestions(channel_conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_data_deletion_status
    ON data_deletion_requests(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_shop_members_user ON shop_members(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash, expires_at);
CREATE INDEX IF NOT EXISTS idx_meta_oauth_states_user
    ON meta_oauth_states(user_id, expires_at);
"""

# Keep JSON and timestamps as text so repository queries remain portable.
POSTGRES_SCHEMA = SCHEMA.replace(
    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
)

SERIAL_TABLES = {
    "shops",
    "products",
    "messages",
    "tool_calls",
    "handoffs",
    "channel_connections",
    "channel_messages",
    "channel_events",
    "copilot_suggestions",
}


class CompatRow(Mapping[str, Any]):
    """Provide sqlite3.Row-style key and positional access for PostgreSQL."""

    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        self._values = values
        self._data = dict(zip(columns, values, strict=True))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class PostgresCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def _wrap(self, row: tuple[Any, ...] | None) -> CompatRow | None:
        if row is None:
            return None
        columns = [column.name for column in self._cursor.description]
        return CompatRow(columns, row)

    def fetchone(self) -> CompatRow | None:
        return self._wrap(self._cursor.fetchone())

    def fetchall(self) -> list[CompatRow]:
        return [self._wrap(row) for row in self._cursor.fetchall()]


class PostgresConnection:
    """Adapt psycopg to the small sqlite3 API used by the repository."""

    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, params: Any = ()) -> PostgresCursor:
        cursor = self._connection.cursor()
        cursor.execute(sql.replace("?", "%s"), tuple(params))
        lastrowid = None
        match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)", sql, re.IGNORECASE)
        if match and match.group(1).lower() in SERIAL_TABLES:
            identity_cursor = self._connection.cursor()
            identity_cursor.execute("SELECT LASTVAL()")
            lastrowid = identity_cursor.fetchone()[0]
            identity_cursor.close()
        return PostgresCursor(cursor, lastrowid)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {} if default is None else default


def using_postgres(path: Path | None = None) -> bool:
    return path is None and bool(getattr(settings, "database_url", None))


def connect(path: Path | None = None) -> sqlite3.Connection | PostgresConnection:
    if using_postgres(path):
        if psycopg is None:
            raise RuntimeError("DATABASE_URL requires the psycopg package.")
        return PostgresConnection(psycopg.connect(settings.database_url))
    db_path = path or settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def db_session(path: Path | None = None) -> Iterator[sqlite3.Connection | PostgresConnection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database(path: Path | None = None) -> None:
    with db_session(path) as connection:
        connection.executescript(POSTGRES_SCHEMA if using_postgres(path) else SCHEMA)


def row_to_dict(row: Mapping[str, Any] | sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def is_integrity_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) or (
        psycopg is not None and isinstance(exc, psycopg.IntegrityError)
    )


def is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE constraint failed" in str(exc)
    return bool(psycopg is not None and getattr(exc, "sqlstate", None) == "23505")
