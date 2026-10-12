"""Shared, atomic monthly AI budget for chat and staff copilot."""
from datetime import UTC, datetime

from app.billing import PLANS
from app.repository import Repository, repository


def usage_status(shop_id: int, repo: Repository = repository) -> dict:
    subscription = repo.get_shop_subscription(shop_id)
    plan = subscription["plan"] if subscription["status"] in {"active", "trialing"} else "free"
    if plan not in PLANS:
        plan = "free"
    period = datetime.now(UTC).strftime("%Y-%m")
    limit = PLANS[plan]["monthly_messages"]
    used = repo.ai_usage(shop_id, period)
    return {"period": period, "effective_plan": plan, "used": used,
            "limit": limit, "remaining": max(0, limit - used)}


def reserve_ai_turn(shop_id: int, repo: Repository = repository) -> bool:
    status = usage_status(shop_id, repo)
    return repo.reserve_ai_turn(shop_id, status["period"], status["limit"])
