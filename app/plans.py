"""Plan entitlements, separate from the payment provider."""

PLANS = {
    "free": {"name": "Free", "monthly_messages": 500, "seats": 1},
    "pro": {"name": "Pro", "monthly_messages": 10000, "seats": 5},
    "business": {"name": "Business", "monthly_messages": 50000, "seats": 20},
}


def effective_plan(subscription: dict, trial: dict | None = None) -> str:
    plan = (
        subscription.get("plan") if subscription.get("status") in {"active", "trialing"} else "free"
    )
    plan = plan if plan in PLANS else "free"
    if trial and trial.get("plan") in PLANS:
        return max((plan, trial["plan"]), key=lambda item: PLANS[item]["seats"])
    return plan
