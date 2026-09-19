from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.agent import sales_agent
from app.channels.base import InboundMessage
from app.channels.meta import MetaMessengerAdapter
from app.channels.web import WebChannelAdapter
from app.config import BASE_DIR, settings
from app.database import init_database
from app.inbox import inbox_service
from app.repository import repository
from app.schemas import (
    BotControlRequest,
    ChatRequest,
    ChatResponse,
    InboxActionRequest,
    ProductCreate,
    HumanReplyRequest,
    ShopCreate,
    WebChannelMessage,
)
from app.seed import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    seed_demo_data()
    if settings.meta_page_id:
        shop = repository.get_shop(settings.meta_shop_slug)
        if shop:
            repository.upsert_channel_connection(
                shop["id"], "messenger", settings.meta_page_id,
                f"Facebook Page {settings.meta_page_id}",
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


@app.get("/privacy", include_in_schema=False)
def privacy_policy() -> FileResponse:
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/terms", include_in_schema=False)
def terms_of_service() -> FileResponse:
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/data-deletion", include_in_schema=False)
def data_deletion_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "data-deletion.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm_configured": bool(settings.groq_api_key),
        "model": settings.groq_model if settings.groq_api_key else "rule-fallback",
        "channels": {
            "web": True,
            "messenger": MetaMessengerAdapter().configured,
        },
    }


@app.get("/api/integrations/meta/status")
def meta_integration_status() -> dict:
    public_url = settings.public_base_url.rstrip("/")
    return {
        "configured": MetaMessengerAdapter().configured,
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


@app.get("/api/shops")
def list_shops() -> list[dict]:
    return repository.list_shops()


@app.post("/api/shops", status_code=201)
def create_shop(payload: ShopCreate) -> dict:
    try:
        return repository.create_shop(payload.model_dump())
    except sqlite3.IntegrityError as exc:
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
def create_product(slug: str, payload: ProductCreate) -> dict:
    shop = require_shop(slug)
    try:
        return repository.create_product(shop["id"], payload.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="SKU đã tồn tại trong shop.") from exc


@app.post("/api/shops/{slug}/products/import", status_code=201)
async def import_products(slug: str, file: UploadFile = File(...)) -> dict:
    shop = require_shop(slug)
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
        except (ValueError, sqlite3.IntegrityError) as exc:
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
    adapter = MetaMessengerAdapter()
    if not adapter.configured:
        raise HTTPException(status_code=503, detail="Kênh Messenger chưa được cấu hình.")
    if not adapter.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Chữ ký webhook không hợp lệ.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook JSON không hợp lệ.") from exc
    shop = require_shop(settings.meta_shop_slug)
    connection = repository.upsert_channel_connection(
        shop["id"], "messenger", settings.meta_page_id or "unknown-page",
        f"Facebook Page {settings.meta_page_id}",
    )
    events = adapter.parse_events(payload)
    for event in events:
        background_tasks.add_task(
            inbox_service.process, shop, event, adapter, connection["id"]
        )
    return {"status": "accepted", "events": len(events)}


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
def list_inbox_conversations(slug: str) -> list[dict]:
    shop = require_shop(slug)
    return repository.list_channel_conversations(shop["id"])


def require_channel_conversation(slug: str, conversation_id: str) -> tuple[dict, dict]:
    shop = require_shop(slug)
    conversation = repository.get_channel_conversation(conversation_id)
    if not conversation or conversation["shop_id"] != shop["id"]:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return shop, conversation


@app.get("/api/shops/{slug}/inbox/conversations/{conversation_id}/messages")
def list_inbox_messages(slug: str, conversation_id: str) -> list[dict]:
    require_channel_conversation(slug, conversation_id)
    return repository.list_channel_messages(conversation_id)


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/read")
def mark_inbox_conversation_read(slug: str, conversation_id: str) -> dict:
    require_channel_conversation(slug, conversation_id)
    return {"read": repository.mark_channel_messages_read(conversation_id)}


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/actions")
def update_inbox_workflow(
    slug: str, conversation_id: str, payload: InboxActionRequest
) -> dict:
    require_channel_conversation(slug, conversation_id)
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
def control_inbox_bot(slug: str, conversation_id: str, payload: BotControlRequest) -> dict:
    require_channel_conversation(slug, conversation_id)
    updated = repository.set_channel_bot(
        conversation_id, payload.enabled, payload.assigned_to
    )
    return updated or {}


@app.post("/api/shops/{slug}/inbox/conversations/{conversation_id}/messages", status_code=201)
async def send_inbox_reply(
    slug: str, conversation_id: str, payload: HumanReplyRequest
) -> dict:
    _, conversation = require_channel_conversation(slug, conversation_id)
    if conversation["channel"] == "messenger":
        adapter = MetaMessengerAdapter()
        if not adapter.configured:
            raise HTTPException(status_code=503, detail="Kênh Messenger chưa được cấu hình.")
    elif conversation["channel"] == "web":
        adapter = WebChannelAdapter()
    else:
        raise HTTPException(status_code=400, detail="Kênh chưa hỗ trợ gửi tin.")
    try:
        return await inbox_service.reply_as_human(
            conversation, payload.message.strip(), payload.agent_name, adapter
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Nền tảng từ chối gửi tin nhắn.") from exc


@app.get("/api/conversations/{conversation_id}/trace")
def conversation_trace(conversation_id: str) -> dict:
    trace = repository.get_trace(conversation_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return trace


@app.get("/api/shops/{slug}/metrics")
def shop_metrics(slug: str) -> dict:
    shop = require_shop(slug)
    return repository.metrics(shop["id"])
