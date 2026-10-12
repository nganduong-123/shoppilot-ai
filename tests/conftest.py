from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest

import app.agent as agent_module
import app.copilot as copilot_module
import app.database as database_module
from app.database import init_database
from app.rate_limit import rate_limiter
from app.seed import seed_demo_data


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(database_path=db_path),
    )
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if test_database_url:
        # A dedicated, disposable test database only, never DATABASE_URL/production.
        import psycopg
        from psycopg import sql

        target = urlparse(test_database_url)
        if target.hostname not in {"localhost", "127.0.0.1", "postgres"} or not target.path.endswith("_test"):
            raise RuntimeError("TEST_DATABASE_URL must be a local dedicated *_test database")
        schema = "test_" + uuid4().hex
        with psycopg.connect(test_database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        monkeypatch.setattr(database_module, "settings", SimpleNamespace(
            database_path=db_path,
            database_url=test_database_url,
            database_schema=schema,
        ))
    offline_settings = SimpleNamespace(
        groq_api_key=None,
        groq_model="test-model",
        groq_base_url="https://invalid.local",
    )
    monkeypatch.setattr(agent_module, "settings", offline_settings)
    monkeypatch.setattr(copilot_module, "settings", offline_settings)
    init_database(db_path)
    if test_database_url:
        init_database()
    rate_limiter.reset()
    seed_demo_data()
    try:
        yield
    finally:
        rate_limiter.reset()
        if test_database_url:
            with psycopg.connect(test_database_url, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
