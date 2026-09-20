from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.agent as agent_module
import app.copilot as copilot_module
import app.database as database_module
from app.database import init_database
from app.seed import seed_demo_data


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(database_path=db_path),
    )
    offline_settings = SimpleNamespace(
        groq_api_key=None,
        groq_model="test-model",
        groq_base_url="https://invalid.local",
    )
    monkeypatch.setattr(agent_module, "settings", offline_settings)
    monkeypatch.setattr(copilot_module, "settings", offline_settings)
    init_database(db_path)
    seed_demo_data()
    yield
