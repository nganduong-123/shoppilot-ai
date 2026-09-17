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
    app_version: str = "0.1.0"
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/shoppilot.db")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    groq_base_url: str = "https://api.groq.com/openai/v1"


settings = Settings()
