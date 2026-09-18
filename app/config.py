from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "ShopPilot AI"
    app_version: str = "0.3.0"
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/shoppilot.db")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    meta_shop_slug: str = os.getenv("META_SHOP_SLUG", "mint-fashion")
    meta_page_id: str | None = os.getenv("META_PAGE_ID")
    meta_app_id: str | None = os.getenv("META_APP_ID")
    meta_app_secret: str | None = os.getenv("META_APP_SECRET")
    meta_page_access_token: str | None = os.getenv("META_PAGE_ACCESS_TOKEN")
    meta_verify_token: str | None = os.getenv("META_VERIFY_TOKEN")
    meta_graph_api_version: str = os.getenv("META_GRAPH_API_VERSION", "v25.0")


settings = Settings()
