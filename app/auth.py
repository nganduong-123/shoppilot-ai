from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, Response

from app.config import settings
from app.repository import Repository, repository

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


class EmailNotVerifiedError(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected_hex)),
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, repo: Repository = repository) -> None:
        self.repo = repo

    def register(
        self, payload: dict[str, Any], *, create_session: bool = True
    ) -> tuple[dict[str, Any], str | None]:
        email = normalize_email(payload["email"])
        user = self.repo.create_user_with_shop(
            email=email,
            display_name=payload["display_name"].strip(),
            password_hash=hash_password(payload["password"]),
            shop={
                "slug": payload["shop_slug"],
                "name": payload["shop_name"].strip(),
                "category": payload["category"].strip(),
                "tagline": f"Trợ lý bán hàng của {payload['shop_name'].strip()}",
                "policy_text": (
                    "Chính sách đang được chủ shop cập nhật. "
                    "Không tự suy đoán giá, tồn kho, phí giao hàng hoặc cam kết đổi trả."
                ),
                "voice": "Thân thiện, ngắn gọn, trung thực và ưu tiên thông tin có căn cứ.",
            },
        )
        if not settings.email_verification_required:
            self.repo.mark_email_verified(user["id"])
        token = self._new_session(user["id"]) if create_session else None
        return self._public_user(user), token

    def login(self, email: str, password: str) -> tuple[dict[str, Any], str] | None:
        user = self.repo.get_user_by_email(normalize_email(email))
        if not user or user["status"] != "active":
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        if settings.email_verification_required and not self.repo.is_email_verified(user["id"]):
            raise EmailNotVerifiedError
        return self._public_user(user), self._new_session(user["id"])

    def issue_account_token(self, user_id: str, purpose: str, minutes: int) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
        self.repo.create_account_token(user_id, purpose, token_digest(token), expires_at)
        return token

    def confirm_email(self, token: str) -> dict[str, Any] | None:
        account = self.repo.consume_account_token(
            token_digest(token), "verify_email", datetime.now(UTC).isoformat()
        )
        if not account:
            return None
        self.repo.mark_email_verified(account["user_id"])
        return account

    def reset_password(self, token: str, new_password: str) -> dict[str, Any] | None:
        account = self.repo.consume_account_token(
            token_digest(token), "password_reset", datetime.now(UTC).isoformat()
        )
        if not account:
            return None
        self.repo.update_user_password(account["user_id"], hash_password(new_password))
        return account

    def _new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(days=settings.session_days)).isoformat()
        self.repo.create_auth_session(user_id, token_digest(token), expires_at)
        return token

    def user_from_request(self, request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                token = authorization[7:].strip()
        if not token:
            return None
        return self.repo.get_user_by_session(token_digest(token), datetime.now(UTC).isoformat())

    def logout(self, request: Request) -> None:
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            self.repo.delete_auth_session(token_digest(token))

    def _public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: user[key]
            for key in ("id", "email", "display_name", "status", "created_at")
        }
        result["email_verified"] = self.repo.is_email_verified(user["id"])
        return result

    @staticmethod
    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            max_age=settings.session_days * 86400,
            httponly=True,
            secure=settings.app_env == "production",
            samesite="lax",
            path="/",
        )

    @staticmethod
    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(settings.session_cookie_name, path="/")


auth_service = AuthService()


def require_user(request: Request) -> dict[str, Any]:
    user = auth_service.user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập ShopPilot.")
    return user
