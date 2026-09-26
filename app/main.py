from __future__ import annotations

import csv
import io
import json
import secrets
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agent import sales_agent
from app.auth import auth_service, require_user
from app.channels.base import InboundMessage
from app.channels.make import MakeMessengerAdapter
from app.channels.meta import MetaMessengerAdapter
from app.channels.web import WebChannelAdapter
from app.config import BASE_DIR, settings
from app.copilot import inbox_copilot
from app.database import init_database, is_integrity_error, using_postgres
from app.inbox import inbox_service
from app.meta_oauth import meta_oauth_service
from app.repository import repository
from app.schemas import (
    BotControlRequest,
    ChatRequest,
    ChatResponse,
    MakeMessengerMessage,
    InboxActionRequest,
    ProductCreate,
    HumanReplyRequest,
    LoginRequest,
    MetaConnectionComplete,
    RegisterRequest,
    ShopCreate,
    WebChannelMessage,
)
from app.seed import seed_demo_data
from app.token_crypto import decrypt_secret


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    seed_demo_data()
    if settings.meta_page_id:
        shop = repository.get_shop(settings.meta_shop_slug)
        existing = repository.get_channel_connection_by_external(
            "messenger", settings.meta_page_id
        )
        if shop and not existing:
            repository.upsert_channel_connection(
                shop["id"], "messenger", settings.meta_page_id,
                f"Facebook Page {settings.meta_page_id}",
                {"source": "environment"},
            )
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Auditable multi-tenant sales and support agent for online shops.",
    lifespan=lifespan,
)

STATIC_DIR = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/privacy", include_in_schema=False)
def privacy_policy() -> FileResponse:
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/terms", include_in_schema=False)
def terms_of_service() -> FileResponse:
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/data-deletion", include_in_schema=False)
def data_deletion_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "data-deletion.html")


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    user = auth_service.user_from_request(request)
    return {
        "authenticated": bool(user),
        "auth_required": settings.auth_required,
        "registration_enabled": settings.registration_enabled,
        "user": user,
        "shops": repository.list_user_shops(user["id"]) if user else [],
    }


@app.post("/api/auth/register", status_code=201)
def register_account(payload: RegisterRequest, response: Response) -> dict:
    if not settings.registration_enabled:
        raise HTTPException(status_code=403, detail="Đăng ký tài khoản đang tạm đóng.")
    try:
        user, token = auth_service.register(payload.model_dump())
    except Exception as exc:
        if not is_integrity_error(exc):
            raise
        raise HTTPException(
            status_code=409, detail="Email hoặc mã cửa hàng đã được sử dụng."
        ) from exc
    auth_service.set_session_cookie(response, token)
    return {"user": user, "shops": repository.list_user_shops(user["id"])}


@app.post("/api/auth/login")
def login_account(payload: LoginRequest, response: Response) -> dict:
    result = auth_service.login(payload.email, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng.")
    user, token = result
    auth_service.set_session_cookie(response, token)
    return {"user": user, "shops": repository.list_user_shops(user["id"])}


@app.post("/api/auth/logout")
def logout_account(request: Request, response: Response) -> dict:
    auth_service.logout(request)
    auth_service.clear_session_cookie(response)
    return {"logged_out": True}


@app.get("/api/health")
def health() -> dict:
    direct_messenger = MetaMessengerAdapter().configured
    make_adapter = MakeMessengerAdapter()
    make_messenger = make_adapter.inbound_configured and make_adapter.outbound_configured
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm_configured": bool(settings.groq_api_key),
        "model": settings.groq_model if settings.groq_api_key else "rule-fallback",
        "storage": "postgresql" if using_postgres() else "sqlite",
        "channels": {
            "web": True,
            "messenger": direct_messenger or make_messenger,
        },
    }


@app.get("/api/integrations/meta/status")
def meta_integration_status(request: Request, shop_slug: str | None = None) -> dict:
    public_url = settings.public_base_url.rstrip("/")
    connections = []
    if shop_slug:
        shop = require_managed_shop(shop_slug, request)
        connections = repository.list_channel_connections(shop["id"], "messenger")
    configured = bool(connections) or MetaMessengerAdapter().configured
    return {
        "configured": configured,
        "oauth_available": meta_oauth_service.configured,
        "connections": [
            {
                "page_id": item["external_account_id"],
                "display_name": item["display_name"],
                "status": item["status"],
                "source": item["config"].get("source", "environment"),
            }
            for item in connections
        ],
        "requirements": {
            "app_id": bool(settings.meta_app_id),
            "app_secret": bool(settings.meta_app_secret),
            "page_id": bool(settings.meta_page_id),
            "page_access_token": bool(settings.meta_page_access_token),
            "verify_token": bool(settings.meta_verify_token),
        },
        "urls": {
            "webhook": f"{public_url}/api/webhooks/meta",
            "privacy": f"{public_url}/privacy",
            "terms": f"{public_url}/terms",
            "data_deletion": f"{public_url}/data-deletion",
            "data_deletion_callback": f"{public_url}/api/meta/data-deletion",
        },
    }


def require_shop_owner(slug: str, request: Request) -> tuple[dict, dict]:
    user = require_user(request)
    shop = require_shop(slug)
    member = repository.get_shop_member(shop["id"], user["id"])
    if not member or member["role"] not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="Chỉ chủ shop hoặc quản lý được kết nối Page.")
    return shop, user


@app.get("/api/shops/{slug}/integrations/meta/connect")
def start_meta_oauth(slug: str, request: Request) -> dict:
    shop, user = require_shop_owner(slug, request)
    try:
        authorization_url = meta_oauth_service.start(user["id"], shop["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"authorization_url": authorization_url}


@app.get("/api/integrations/meta/callback", include_in_schema=False)
async def meta_oauth_callback(request: Request, state: str, code: str) -> RedirectResponse:
    user = require_user(request)
    try:
        await meta_oauth_service.exchange_code(user["id"], state, code)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        return RedirectResponse(
            f"/static/meta-connect.html?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/static/meta-connect.html?state={quote(state)}", status_code=303)


@app.get("/api/integrations/meta/candidates")
def meta_oauth_candidates(request: Request, state: str) -> dict:
    user = require_user(request)
    try:
        return meta_oauth_service.candidates(user["id"], state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/shops/{slug}/integrations/meta/complete")
async def complete_meta_oauth(
    slug: str, payload: MetaConnectionComplete, request: Request
) -> dict:
    shop, user = require_shop_owner(slug, request)
    try:
        return await meta_oauth_service.complete(
            user["id"], shop["id"], payload.state, payload.page_id
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Meta từ chối đăng ký webhook cho Page này."
        ) from exc


@app.get("/api/integrations/make/status")
def make_integration_status() -> dict:
    public_url = settings.public_base_url.rstrip("/")
    adapter = MakeMessengerAdapter()
    return {
        "configured": adapter.inbound_configured and adapter.outbound_configured,
        "inbound_configured": adapter.inbound_configured,
        "outbound_configured": adapter.outbound_configured,
        "inbound_url": f"{public_url}/api/bridges/make/messenger/{settings.meta_shop_slug}",
        "auth_header": "X-ShopPilot-Bridge-Key",
    }


def authenticated_user_if_required(request: Request) -> dict | None:
    user = auth_service.user_from_request(request)
    if settings.auth_required and not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập ShopPilot.")
    return user


def require_managed_shop(slug: str, request: Request) -> dict:
    shop = require_shop(slug)
    user = authenticated_user_if_required(request)
    if settings.auth_required and user and not repository.get_shop_member(shop["id"], user["id"]):
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập cửa hàng này.")
    return shop


@app.get("/api/shops")
def list_shops(request: Request) -> list[dict]:
    user = authenticated_user_if_required(request)
    if settings.auth_required and user:
        return repository.list_user_shops(user["id"])
    return repository.list_shops()


@app.post("/api/shops", status_code=201)
def create_shop(payload: ShopCreate, request: Request) -> dict:
    user = authenticated_user_if_required(request)
    try:
        shop = repository.create_shop(payload.model_dump())
        if user:
            repository.add_shop_member(shop["id"], user["id"], "owner")
        return shop
    except Exception as exc:
        if not is_integrity_error(exc):
            raise
        raise HTTPException(status_code=409, detail="Slug của shop đã tồn tại.") from exc


def require_shop(slug: str) -> dict:
    shop = repository.get_shop(slug)
    if not shop:
        raise HTTPException(status_code=404, detail="Không tìm thấy shop.")
    return shop


@app.get("/api/shops/{slug}")
def get_shop(slug: str) -> dict:
    return require_shop(slug)


@app.get("/api/shops/{slug}/products")
def list_products(slug: str) -> list[dict]:
    shop = require_shop(slug)
    return repository.list_products(shop["id"])


@app.post("/api/shops/{slug}/products", status_code=201)
def create_product(slug: str, payload: ProductCreate, request: Request) -> dict:
    shop = require_managed_shop(slug, request)
    try:
        return repository.create_product(shop["id"], payload.model_dump())
    except Exception as exc:
        if not is_integrity_error(exc):
            raise
        raise HTTPException(status_code=409, detail="SKU đã tồn tại trong shop.") from exc


@app.post("/api/shops/{slug}/products/import", status_code=201)
async def import_products(slug: str, request: Request, file: UploadFile = File(...)) -> dict:
    shop = require_managed_shop(slug, request)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Vui lòng tải file CSV.")
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    required = {"sku", "name", "category", "price", "stock"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=400,
            detail="CSV cần các cột: sku, name, category, price, stock.",
        )
    imported = 0
    errors = []
    for line_number, row in enumerate(reader, 2):
        try:
            attributes = {
                key.removeprefix("attr_"): value
                for key, value in row.items()
                if key.startswith("attr_") and value
            }
            repository.create_product(
                shop["id"],
                {
                    "sku": row["sku"],
                    "name": row["name"],
                    "category": row["category"],
                    "description": row.get("description", ""),
                    "price": int(row["price"]),
                    "stock": int(row["stock"]),
                    "attributes": attributes,
                },
            )
            imported += 1
        except Exception as exc:
            if not isinstance(exc, ValueError) and not is_integrity_error(exc):
                raise
            errors.append({"line": line_number, "error": str(exc)})
    return {"imported": imported, "errors": errors}


@app.post("/api/shops/{slug}/chat", response_model=ChatResponse)
async def chat(slug: str, payload: ChatRequest) -> dict:
    shop = require_shop(slug)
    return await sales_agent.respond(shop, payload.message.strip(), payload.conversation_id)


@app.post("/api/channels/web/{slug}/messages")
async def web_channel_message(slug: str, payload: WebChannelMessage) -> dict:
    shop = require_shop(slug)
    external_conversation_id = payload.conversation_id or str(uuid4())
    customer_id = payload.customer_id or f"web:{external_conversation_id}"
    inbound = InboundMessage(
        channel="web",
        external_event_id=str(uuid4()),
        external_conversation_id=external_conversation_id,
        external_customer_id=customer_id,
        customer_name=payload.customer_name,
        text=payload.message.strip(),
        metadata={"source": "website-widget"},
    )
    result = await inbox_service.process(shop, inbound, WebChannelAdapter())
    result["conversation_id"] = external_conversation_id
    return result


@app.post("/api/bridges/make/messenger/{slug}")
async def make_messenger_message(
    slug: str, payload: MakeMessengerMessage, request: Request
) -> dict:
    adapter = MakeMessengerAdapter(response_only=True)
    if not adapter.inbound_configured:
        raise HTTPException(status_code=503, detail="Cầu nối Make chưa được cấu hình.")
    supplied_key = request.headers.get("X-ShopPilot-Bridge-Key", "")
    if not secrets.compare_digest(supplied_key, settings.make_bridge_secret or ""):
        raise HTTPException(status_code=401, detail="Khóa cầu nối Make không hợp lệ.")

    shop = require_shop(slug)
    connection = repository.upsert_channel_connection(
        shop["id"],
        "messenger",
        payload.page_id,
        f"Messenger via Make · {payload.page_id}",
    )
    inbound = InboundMessage(
        channel="messenger",
        external_event_id=f"make:{payload.event_id}",
        external_conversation_id=f"{payload.page_id}:{payload.sender_id}",
        external_customer_id=payload.sender_id,
        customer_name=payload.customer_name,
        text=payload.message.strip(),
        metadata={"source": "make", "page_id": payload.page_id},
    )
    result = await inbox_service.process(shop, inbound, adapter, connection["id"])
    return {
        "status": result["status"],
        "replied": result.get("replied", False),
        "recipient_id": payload.sender_id,
        "reply": result.get("message"),
        "channel_conversation_id": result.get("channel_conversation_id"),
    }


@app.get("/api/channels/web/{slug}/conversations/{external_id}/messages")
def web_channel_history(slug: str, external_id: str) -> list[dict]:
    shop = require_shop(slug)
    conversation = repository.get_channel_conversation_by_external(
        shop["id"], "web", external_id
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return repository.list_channel_messages(conversation["id"])


@app.get("/api/webhooks/meta", response_class=PlainTextResponse)
def verify_meta_webhook(request: Request) -> PlainTextResponse:
    adapter = MetaMessengerAdapter()
    params = request.query_params
    if not adapter.verify_challenge(params.get("hub.mode"), params.get("hub.verify_token")):
        raise HTTPException(status_code=403, detail="Meta webhook verification failed.")
    return PlainTextResponse(params.get("hub.challenge", ""))


@app.post("/api/webhooks/meta")
async def receive_meta_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()
    signature_adapter = MetaMessengerAdapter()
    if not settings.meta_app_secret:
        raise HTTPException(status_code=503, detail="Kênh Messenger chưa được cấu hình.")
    if not signature_adapter.verify_signature(
        body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="Chữ ký webhook không hợp lệ.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook JSON không hợp lệ.") from exc
    accepted = 0
    for entry in payload.get("entry", []):
        page_id = str(entry.get("id") or "")
        connection = repository.get_channel_connection_by_external("messenger", page_id)
        shop = repository.get_shop_by_id(connection["shop_id"]) if connection else None
        page_token = None
        if connection and connection["config"].get("page_access_token_enc"):
            page_token = decrypt_secret(connection["config"]["page_access_token_enc"])
        elif page_id == settings.meta_page_id:
            shop = require_shop(settings.meta_shop_slug)
            page_token = settings.meta_page_access_token
            connection = repository.upsert_channel_connection(
                shop["id"], "messenger", page_id, f"Facebook Page {page_id}"
            )
        if not shop or not connection or not page_token:
            continue
        adapter = MetaMessengerAdapter(page_id=page_id, page_access_token=page_token)
        events = adapter.parse_events({"object": payload.get("object"), "entry": [entry]})
        for event in events:
            background_tasks.add_task(
                inbox_service.process, shop, event, adapter, connection["id"]
            )
        accepted += len(events)
    return {"status": "accepted", "events": accepted}


@app.post("/api/meta/data-deletion")
async def receive_meta_data_deletion(request: Request) -> dict:
    form = await request.form()
    signed_request = form.get("signed_request")
    if not isinstance(signed_request, str) or not signed_request:
        raise HTTPException(status_code=400, detail="Thiếu Meta signed_request.")
    try:
        payload = MetaMessengerAdapter().parse_signed_request(signed_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    confirmation_code = secrets.token_urlsafe(18)
    repository.delete_external_customer_data(
        channel="messenger",
        external_customer_id=str(payload["user_id"]),
        confirmation_code=confirmation_code,
    )
    status_url = (
        f"{settings.public_base_url.rstrip('/')}/data-deletion"
        f"?code={quote(confirmation_code)}"
    )
    return {"url": status_url, "confirmation_code": confirmation_code}


@app.get("/api/data-deletion/status/{confirmation_code}")
def data_deletion_status(confirmation_code: str) -> dict:
    if len(confirmation_code) > 100:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu xóa dữ liệu.")
    result = repository.get_data_deletion_status(confirmation_code)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu xóa dữ liệu.")
    return result


@app.get("/api/shops/{slug}/inbox/conversations")
def list_inbox_conversations(slug: str, request: Request) -> list[dict]:
    shop = require_managed_shop(slug, request)
    return repository.list_channel_conversations(shop["id"])


def require_channel_conversation(
    slug: str, conversation_id: str, request: Request | None = None
) -> tuple[dict, dict]:
    shop = require_managed_shop(slug, request) if request else require_shop(slug)
    conversation = repository.get_channel_conversation(conversation_id)
    if not conversation or conversation["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return shop, conversation


@app.get("/api/shops/{slug}/inbox/conversations/{conversation_id}/messages")
def list_inbox_messages(slug: str, conversation_id: str, request: Request) -> list[dict]:
    require_channel_conversation(slug, conversation_id, request)
    return repository.list_channel_messages(conversation_id)


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/read")
def mark_inbox_conversation_read(slug: str, conversation_id: str, request: Request) -> dict:
    require_channel_conversation(slug, conversation_id, request)
    return {"read": repository.mark_channel_messages_read(conversation_id)}


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/assist")
async def create_inbox_copilot_suggestion(
    slug: str, conversation_id: str, request: Request
) -> dict:
    shop, conversation = require_channel_conversation(slug, conversation_id, request)
    try:
        return await inbox_copilot.suggest(shop, conversation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/actions")
def update_inbox_workflow(
    slug: str, conversation_id: str, payload: InboxActionRequest, request: Request
) -> dict:
    require_channel_conversation(slug, conversation_id, request)
    if payload.action == "takeover":
        updated = repository.update_channel_workflow(
            conversation_id,
            status="open",
            assigned_to=payload.agent_name,
            bot_enabled=False,
        )
    elif payload.action == "resolve":
        updated = repository.update_channel_workflow(
            conversation_id,
            status="resolved",
            assigned_to=None,
            bot_enabled=True,
        )
    else:
        updated = repository.update_channel_workflow(
            conversation_id,
            status="open",
            assigned_to=payload.agent_name,
            bot_enabled=False,
        )
    return updated or {}


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/bot")
def control_inbox_bot(
    slug: str, conversation_id: str, payload: BotControlRequest, request: Request
) -> dict:
    require_channel_conversation(slug, conversation_id, request)
    updated = repository.set_channel_bot(
        conversation_id, payload.enabled, payload.assigned_to
    )
    return updated or {}


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/messages", status_code=201)
async def send_inbox_reply(
    slug: str, conversation_id: str, payload: HumanReplyRequest, request: Request
) -> dict:
    _, conversation = require_channel_conversation(slug, conversation_id, request)
    if conversation["channel"] == "messenger":
        if conversation.get("metadata", {}).get("source") == "make":
            adapter = MakeMessengerAdapter()
            if not adapter.outbound_configured:
                raise HTTPException(
                    status_code=503,
                    detail="Webhook gửi ra của Make chưa được cấu hình.",
                )
        else:
            connection = repository.get_channel_connection(conversation["connection_id"])
            token_enc = connection.get("config", {}).get("page_access_token_enc") if connection else None
            adapter = MetaMessengerAdapter(
                page_id=connection["external_account_id"] if connection else None,
                page_access_token=decrypt_secret(token_enc) if token_enc else None,
            )
            if not adapter.configured:
                raise HTTPException(status_code=503, detail="Kênh Messenger chưa được cấu hình.")
    elif conversation["channel"] == "web":
        adapter = WebChannelAdapter()
    else:
        raise HTTPException(status_code=400, detail="Kênh chưa hỗ trợ gửi tin.")
    try:
        message = await inbox_service.reply_as_human(
            conversation, payload.message.strip(), payload.agent_name, adapter
        )
        if payload.suggestion_id:
            message["copilot_used"] = repository.mark_copilot_suggestion_used(
                payload.suggestion_id, conversation_id
            )
        return message
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Nền tảng từ chối gửi tin nhắn.") from exc


@app.get("/api/conversations/{conversation_id}/trace")
def conversation_trace(conversation_id: str, request: Request) -> dict:
    trace = repository.get_trace(conversation_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    user = authenticated_user_if_required(request)
    if settings.auth_required and user and not repository.get_shop_member(
        trace["conversation"]["shop_id"], user["id"]
    ):
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập hội thoại này.")
    return trace


@app.get("/api/shops/{slug}/metrics")
def shop_metrics(slug: str, request: Request) -> dict:
    shop = require_managed_shop(slug, request)
    return repository.metrics(shop["id"])
