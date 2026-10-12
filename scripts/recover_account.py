"""Operator-only recovery using trusted database access, never a public HTTP route."""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse


def issue_recovery_link(email: str, expected_shop: str, base_url: str) -> tuple[str, str]:
    from app.auth import auth_service
    from app.repository import repository

    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use the verified HTTPS production URL.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use a base URL without a path, query or fragment.")
    user = repository.get_user_by_email(email.strip().casefold())
    if not user or user["status"] != "active":
        raise ValueError("Active account not found; no account was changed.")
    shops = repository.list_user_shops(user["id"])
    if not any(shop["slug"] == expected_shop and shop["role"] == "owner" for shop in shops):
        raise ValueError("Expected shop ownership does not match; no account was changed.")
    token = auth_service.issue_account_token(user["id"], "password_reset", 15)
    return f"{base_url.rstrip('/')}/login?reset={token}", user["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--expected-shop", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--issue-reset-link", action="store_true", required=True)
    parser.add_argument("--database-url-stdin", action="store_true")
    args = parser.parse_args()
    if args.database_url_stdin:
        os.environ["DATABASE_URL"] = sys.stdin.read().strip()
    if not os.environ.get("DATABASE_URL"):
        parser.error("Trusted production DATABASE_URL is required.")
    try:
        link, user_id = issue_recovery_link(args.email, args.expected_shop, args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    from app.database import utc_now

    print(json.dumps({
        "event": "operator_account_recovery", "user_id": user_id,
        "shop": args.expected_shop, "reason": args.reason, "time": utc_now(),
    }), file=sys.stderr)
    print(link)


if __name__ == "__main__":
    main()
