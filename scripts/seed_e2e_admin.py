"""Create a disposable browser-test operator, never on a production service."""

import os
from urllib.parse import urlparse


def main():
    if os.getenv("SHOPPILOT_TEST_ADMIN") != "true":
        raise RuntimeError("Explicit SHOPPILOT_TEST_ADMIN=true is required")
    from app.auth import auth_service
    from app.config import settings
    from app.database import init_database
    from app.repository import repository

    if settings.app_env != "development" or settings.email_verification_required:
        raise RuntimeError("Fixture is restricted to an offline development server")
    if settings.database_url:
        target = urlparse(settings.database_url)
        if target.hostname not in {
            "localhost",
            "127.0.0.1",
            "postgres",
            "host.docker.internal",
        } or not target.path.endswith("_test"):
            raise RuntimeError("Fixture requires a local dedicated *_test database")
    if settings.groq_api_key or settings.stripe_secret_key or settings.meta_page_access_token:
        raise RuntimeError("Fixture refuses a server with live provider credentials")
    init_database()
    email = "e2e-platform@example.com"
    if repository.get_user_by_email(email):
        raise RuntimeError("Fixture requires a fresh test operator; no existing account is altered")
    auth_service.register(
        {
            "display_name": "Browser Test Operator",
            "email": email,
            "password": "e2e-platform-test-password",
            "shop_name": "Browser Operator Shop",
            "shop_slug": "e2e-platform-shop",
            "category": "Test",
        },
        create_session=False,
    )
    repository.grant_platform_admin(email, "Disposable browser release gate fixture")
    print("Disposable platform test operator created")


if __name__ == "__main__":
    main()
