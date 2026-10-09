from __future__ import annotations

import html
import logging
from urllib.parse import quote

import httpx

from app.config import Settings, settings

logger = logging.getLogger("shoppilot.email")


class EmailService:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        if self.config.email_provider == "resend":
            return bool(self.config.resend_api_key and self.config.email_from)
        return self.config.email_provider == "console" and self.config.app_env != "production"

    def send_verification(self, email: str, display_name: str, token: str) -> bool:
        url = (
            f"{self.config.public_base_url.rstrip('/')}/login"
            f"?verify={quote(token)}"
        )
        return self._send(
            email,
            "Xác minh email ShopPilot AI",
            display_name,
            "Xác minh email",
            "Bấm nút dưới đây để kích hoạt tài khoản ShopPilot.",
            url,
        )

    def send_password_reset(self, email: str, display_name: str, token: str) -> bool:
        url = (
            f"{self.config.public_base_url.rstrip('/')}/login"
            f"?reset={quote(token)}"
        )
        return self._send(
            email,
            "Đặt lại mật khẩu ShopPilot AI",
            display_name,
            "Đặt lại mật khẩu",
            "Liên kết này chỉ dùng một lần và sẽ sớm hết hạn.",
            url,
        )

    def _send(
        self,
        recipient: str,
        subject: str,
        display_name: str,
        action: str,
        message: str,
        url: str,
    ) -> bool:
        if not self.configured:
            return False
        if self.config.email_provider == "console":
            logger.info("%s for %s: %s", subject, recipient, url)
            return True

        safe_name = html.escape(display_name)
        safe_message = html.escape(message)
        safe_url = html.escape(url, quote=True)
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self.config.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": self.config.email_from,
                "to": [recipient],
                "subject": subject,
                "html": (
                    f"<p>Chào {safe_name},</p><p>{safe_message}</p>"
                    f'<p><a href="{safe_url}">{html.escape(action)}</a></p>'
                    "<p>Nếu bạn không yêu cầu thao tác này, hãy bỏ qua email.</p>"
                ),
            },
            timeout=15,
        )
        response.raise_for_status()
        return True


email_service = EmailService()
