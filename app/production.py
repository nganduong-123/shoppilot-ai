"""Fail closed on unsafe production configuration, without printing secrets."""
from urllib.parse import urlparse

from app.config import Settings


def production_errors(config: Settings) -> list[str]:
    if config.app_env != "production":
        return []
    errors = []
    if not config.auth_required:
        errors.append("AUTH_REQUIRED must be true")
    if not config.database_url or not config.database_url.startswith(("postgres://", "postgresql://")):
        errors.append("DATABASE_URL must point to persistent PostgreSQL")
    url = urlparse(config.public_base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/")):
        errors.append("PUBLIC_BASE_URL must be an HTTPS origin")
    if not config.token_encryption_key or len(config.token_encryption_key) < 32:
        errors.append("TOKEN_ENCRYPTION_KEY must contain at least 32 characters")
    if not config.rate_limit_enabled:
        errors.append("RATE_LIMIT_ENABLED must be true")
    if config.email_verification_required and not (
        config.email_provider == "resend" and config.resend_api_key
        and "example.com" not in config.email_from
    ):
        errors.append("Required email verification needs a configured sender")
    return errors
