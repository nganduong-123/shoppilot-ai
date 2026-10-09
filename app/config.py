from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "ShopPilot AI"
    app_version: str = "1.0.0"
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/shoppilot.db")
    database_url: str | None = os.getenv("DATABASE_URL")
    auth_required: bool = env_bool("AUTH_REQUIRED", False)
    registration_enabled: bool = env_bool("REGISTRATION_ENABLED", True)
    email_verification_required: bool = env_bool("EMAIL_VERIFICATION_REQUIRED", False)
    email_provider: str = os.getenv("EMAIL_PROVIDER", "console").strip().lower()
    email_from: str = os.getenv("EMAIL_FROM", "ShopPilot AI <noreply@example.com>")
    resend_api_key: str | None = os.getenv("RESEND_API_KEY")
    verification_token_minutes: int = int(os.getenv("VERIFICATION_TOKEN_MINUTES", "60"))
    password_reset_token_minutes: int = int(
        os.getenv("PASSWORD_RESET_TOKEN_MINUTES", "30")
    )
    rate_limit_enabled: bool = env_bool("RATE_LIMIT_ENABLED", True)
    auth_rate_limit_requests: int = int(os.getenv("AUTH_RATE_LIMIT_REQUESTS", "10"))
    auth_rate_limit_window_seconds: int = int(
        os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "300")
    )
    chat_rate_limit_requests: int = int(os.getenv("CHAT_RATE_LIMIT_REQUESTS", "60"))
    chat_rate_limit_window_seconds: int = int(
        os.getenv("CHAT_RATE_LIMIT_WINDOW_SECONDS", "60")
    )
    max_csv_upload_bytes: int = int(os.getenv("MAX_CSV_UPLOAD_BYTES", "2000000"))
    max_csv_rows: int = int(os.getenv("MAX_CSV_ROWS", "10000"))
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "shoppilot_session")
    session_days: int = int(os.getenv("SESSION_DAYS", "14"))
    token_encryption_key: str | None = os.getenv("TOKEN_ENCRYPTION_KEY")
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
    meta_graph_api_version: str = os.getenv("META_GRAPH_API_VERSION", "v26.0")
    make_bridge_secret: str | None = os.getenv("MAKE_BRIDGE_SECRET")
    make_messenger_outbound_webhook_url: str | None = os.getenv(
        "MAKE_MESSENGER_OUTBOUND_WEBHOOK_URL"
    )
    shipping_quote_url: str | None = os.getenv("SHIPPING_QUOTE_URL")
    commerce_order_webhook_url: str | None = os.getenv("COMMERCE_ORDER_WEBHOOK_URL")
    integration_webhook_secret: str | None = os.getenv("INTEGRATION_WEBHOOK_SECRET")


settings = Settings()
