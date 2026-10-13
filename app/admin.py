"""Platform operations, separate from tenant owner permissions."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth import require_user
from app.billing import billing_service
from app.config import BASE_DIR, settings
from app.database import db_session, utc_now
from app.email_service import email_service
from app.meta_oauth import meta_oauth_service
from app.production import production_errors
from app.repository import repository

router = APIRouter()


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not repository.is_platform_admin(user["id"]):
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên nền tảng được truy cập.")
    return user


def audit(connection, actor_id, action, target_id, reason):
    connection.execute(
        "INSERT INTO platform_audit (id, actor_id, action, target_id, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid4()), actor_id, action, str(target_id), reason, utc_now()),
    )


class StatusUpdate(BaseModel):
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=5, max_length=500)


class TrialGrant(BaseModel):
    plan: Literal["pro", "business"]
    days: int = Field(default=7, ge=1, le=30)
    reason: str = Field(min_length=5, max_length=500)


class RetryJob(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    require_admin(request)
    return FileResponse(BASE_DIR / "app/static/admin.html")


@router.get("/api/admin/overview")
def overview(request: Request):
    require_admin(request)
    with db_session() as connection:
        row = connection.execute(
            """
            SELECT (SELECT COUNT(*) FROM users) AS users,
                (SELECT COUNT(*) FROM users WHERE status = 'suspended') AS suspended,
                (SELECT COUNT(*) FROM shops) AS shops,
                (SELECT COUNT(*) FROM jobs WHERE status = 'failed') AS failed_jobs,
                (SELECT COALESCE(SUM(used), 0) FROM shop_ai_usage WHERE period = ?) AS ai_turns
        """,
            (datetime.now(UTC).strftime("%Y-%m"),),
        ).fetchone()
    return dict(row)


@router.get("/api/admin/readiness")
def platform_readiness(request: Request):
    require_admin(request)
    with db_session() as connection:
        connected_pages = connection.execute(
            "SELECT COUNT(*) FROM channel_connections "
            "WHERE channel = 'messenger' AND status = 'active'"
        ).fetchone()[0]
    persistent_database = bool(
        settings.database_url and settings.database_url.startswith(("postgres://", "postgresql://"))
    )
    production_safe = not production_errors(settings)
    fulfillment_ready = bool(
        settings.shipping_quote_url
        and settings.commerce_order_webhook_url
        and settings.integration_webhook_secret
    )
    return {
        "release": settings.app_version,
        "checks": [
            {
                "key": "production",
                "label": "Cấu hình máy chủ",
                "ready": production_safe,
                "detail": "Các điều kiện bảo mật bắt buộc đã đạt."
                if production_safe
                else "Còn cấu hình production chưa đạt.",
            },
            {
                "key": "database",
                "label": "PostgreSQL bền vững",
                "ready": persistent_database,
                "detail": "Đang dùng PostgreSQL."
                if persistent_database
                else "Chưa dùng PostgreSQL.",
            },
            {
                "key": "ai",
                "label": "AI Groq",
                "ready": bool(settings.groq_api_key),
                "detail": settings.groq_model
                if settings.groq_api_key
                else "Đang dùng rule fallback.",
            },
            {
                "key": "email",
                "label": "Email tài khoản",
                "ready": email_service.configured,
                "detail": "Resend đã cấu hình."
                if email_service.configured
                else "Chưa có Resend/sender.",
            },
            {
                "key": "meta",
                "label": "Facebook Messenger",
                "ready": meta_oauth_service.configured and connected_pages > 0,
                "detail": f"{connected_pages} Page đang kết nối."
                if connected_pages
                else "Chưa có Page hoạt động.",
            },
            {
                "key": "stripe",
                "label": "Thanh toán Stripe",
                "ready": billing_service.configured,
                "detail": "Checkout/webhook đã cấu hình."
                if billing_service.configured
                else "Chưa có Stripe key/price.",
            },
            {
                "key": "fulfillment",
                "label": "Giao vận/đơn thật",
                "ready": fulfillment_ready,
                "detail": "Webhook giao vận và đơn hàng đã cấu hình."
                if fulfillment_ready
                else "Đang dùng quy tắc nội bộ; chưa nối hệ thống giao vận.",
            },
        ],
    }


@router.get("/api/admin/shops")
def shops(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    require_admin(request)
    with db_session() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.slug, s.name, s.created_at,
                (SELECT COUNT(*) FROM shop_members m WHERE m.shop_id = s.id) AS members,
                COALESCE(b.plan, 'free') AS plan, COALESCE(b.status, 'active') AS billing_status,
                t.plan AS trial_plan, t.expires_at AS trial_expires_at
            FROM shops s LEFT JOIN shop_subscriptions b ON b.shop_id = s.id
            LEFT JOIN shop_plan_trials t ON t.shop_id = s.id
            ORDER BY s.id DESC LIMIT ? OFFSET ?
        """,
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/api/admin/users")
def users(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    require_admin(request)
    with db_session() as connection:
        rows = connection.execute(
            """
            SELECT u.id, u.email, u.display_name, u.status, u.created_at,
                CASE WHEN a.user_id IS NULL THEN 0 ELSE 1 END AS platform_admin
            FROM users u LEFT JOIN platform_admins a ON a.user_id = u.id
            ORDER BY u.created_at DESC, u.id LIMIT ? OFFSET ?
        """,
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


@router.patch("/api/admin/users/{user_id}/status")
def change_status(user_id: str, payload: StatusUpdate, request: Request):
    actor = require_admin(request)
    with db_session() as connection:
        # No admin (including the caller) can be locked out through this endpoint.
        if connection.execute(
            "SELECT 1 FROM platform_admins WHERE user_id = ?", (user_id,)
        ).fetchone():
            raise HTTPException(
                status_code=409, detail="Không thể khóa tài khoản quản trị tại đây."
            )
        changed = connection.execute(
            "UPDATE users SET status = ? WHERE id = ?", (payload.status, user_id)
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
        if payload.status == "suspended":
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        audit(connection, actor["id"], "user." + payload.status, user_id, payload.reason)
    return {"updated": True, "status": payload.status}


@router.post("/api/admin/shops/{shop_id}/trial")
def grant_trial(shop_id: int, payload: TrialGrant, request: Request):
    actor = require_admin(request)
    expires = (datetime.now(UTC) + timedelta(days=payload.days)).isoformat()
    with db_session() as connection:
        locked = connection.execute("UPDATE shops SET slug = slug WHERE id = ?", (shop_id,))
        if locked.rowcount != 1:
            raise HTTPException(status_code=404, detail="Không tìm thấy shop.")
        connection.execute(
            """
            INSERT INTO shop_plan_trials (shop_id, plan, expires_at, granted_by, reason)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(shop_id) DO UPDATE SET
                plan = excluded.plan, expires_at = excluded.expires_at,
                granted_by = excluded.granted_by, reason = excluded.reason
        """,
            (shop_id, payload.plan, expires, actor["id"], payload.reason),
        )
        audit(connection, actor["id"], "shop.trial." + payload.plan, shop_id, payload.reason)
    return {"granted": True, "expires_at": expires}


@router.get("/api/admin/jobs")
def failed_jobs(request: Request, limit: int = Query(50, ge=1, le=100)):
    require_admin(request)
    with db_session() as connection:
        rows = connection.execute(
            """
            SELECT id, kind, status, attempts, max_attempts, updated_at
            FROM jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT ?
        """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/admin/jobs/{job_id}/retry")
def retry_job(job_id: str, payload: RetryJob, request: Request):
    actor = require_admin(request)
    with db_session() as connection:
        changed = connection.execute(
            """
            UPDATE jobs SET status = 'queued', attempts = 0, locked_at = NULL,
                last_error = NULL, available_at = ?, updated_at = ?
            WHERE id = ? AND status = 'failed' AND kind IN ('meta_message', 'commerce_order')
        """,
            (utc_now(), utc_now(), job_id),
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="Job không ở trạng thái được phép thử lại.")
        audit(connection, actor["id"], "job.retry", job_id, payload.reason)
    return {"queued": True}


@router.get("/api/admin/audit")
def audit_history(request: Request, limit: int = Query(50, ge=1, le=100)):
    require_admin(request)
    with db_session() as connection:
        rows = connection.execute(
            "SELECT * FROM platform_audit ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
