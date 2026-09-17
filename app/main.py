from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent import sales_agent
from app.config import BASE_DIR, settings
from app.database import init_database
from app.repository import repository
from app.schemas import ChatRequest, ChatResponse, ProductCreate, ShopCreate
from app.seed import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    seed_demo_data()
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


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm_configured": bool(settings.groq_api_key),
        "model": settings.groq_model if settings.groq_api_key else "rule-fallback",
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
