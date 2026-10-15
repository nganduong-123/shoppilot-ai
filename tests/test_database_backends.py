from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from app.database import POSTGRES_SCHEMA, SCHEMA, CompatRow, PostgresConnection, init_database


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.description = [SimpleNamespace(name="value")]
        self.rowcount = 1
        self.executed = None
        self.closed = False

    def execute(self, sql, params=()):
        self.executed = (sql, params)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [] if self.row is None else [self.row]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, *cursors):
        self.cursors = list(cursors)

    def cursor(self):
        return self.cursors.pop(0)


def test_postgres_schema_replaces_sqlite_autoincrement():
    assert "AUTOINCREMENT" not in POSTGRES_SCHEMA
    assert "SERIAL PRIMARY KEY" in POSTGRES_SCHEMA


def test_postgres_adapter_translates_placeholders_and_wraps_rows():
    cursor = FakeCursor((7,))
    connection = PostgresConnection(FakeConnection(cursor))

    row = connection.execute("SELECT ? AS value", [7]).fetchone()

    assert cursor.executed == ("SELECT %s AS value", (7,))
    assert isinstance(row, CompatRow)
    assert row["value"] == 7
    assert row[0] == 7
    assert dict(row) == {"value": 7}


def test_postgres_adapter_exposes_generated_identity():
    insert_cursor = FakeCursor()
    identity_cursor = FakeCursor((42,))
    connection = PostgresConnection(FakeConnection(insert_cursor, identity_cursor))

    cursor = connection.execute("INSERT INTO products (name) VALUES (?)", ["Demo"])

    assert insert_cursor.executed == (
        "INSERT INTO products (name) VALUES (%s)",
        ("Demo",),
    )
    assert identity_cursor.executed == ("SELECT LASTVAL()", ())
    assert identity_cursor.closed is True
    assert cursor.lastrowid == 42


def test_session_migration_preserves_existing_sessions_and_is_repeatable(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO users (id,email,display_name,password_hash,created_at) VALUES (?,?,?,?,?)",
            ("legacy-user", "legacy@example.com", "Legacy User", "hash", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO auth_sessions (id,user_id,token_hash,expires_at,created_at) "
            "VALUES (?,?,?,?,?)",
            ("legacy-session", "legacy-user", "token-digest", "2027-01-01", "2026-01-01"),
        )
    init_database(legacy_path)
    init_database(legacy_path)
    with sqlite3.connect(legacy_path) as connection:
        assert connection.execute(
            "SELECT id, token_hash, user_agent FROM auth_sessions"
        ).fetchall() == [("legacy-session", "token-digest", None)]
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            ("002_auth_session_user_agent",),
        ).fetchone()[0] == 1
