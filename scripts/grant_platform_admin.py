"""Trusted operator bootstrap; no public endpoint can grant platform privileges."""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--grant-platform-admin", action="store_true", required=True)
    parser.add_argument("--database-url-stdin", action="store_true")
    args = parser.parse_args()
    if args.database_url_stdin:
        os.environ["DATABASE_URL"] = sys.stdin.read().strip()
    if not os.getenv("DATABASE_URL"):
        parser.error("Explicit trusted production DATABASE_URL is required")
    from app.repository import repository

    try:
        user_id = repository.grant_platform_admin(args.email, args.reason)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"granted": True, "user_id": user_id}))


if __name__ == "__main__":
    main()
