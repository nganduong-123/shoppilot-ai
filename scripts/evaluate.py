from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.agent as agent_module  # noqa: E402
import app.database as database_module  # noqa: E402
from app.agent import sales_agent  # noqa: E402
from app.database import init_database  # noqa: E402
from app.repository import repository  # noqa: E402
from app.seed import seed_demo_data  # noqa: E402


async def evaluate(online: bool = False) -> dict:
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text(encoding="utf-8"))
    original_db_settings = database_module.settings
    original_agent_settings = agent_module.settings

    with tempfile.TemporaryDirectory() as temp_dir:
        database_module.settings = SimpleNamespace(database_path=Path(temp_dir) / "eval.db")
        if not online:
            agent_module.settings = SimpleNamespace(
                groq_api_key=None,
                groq_model="rule-fallback",
                groq_base_url="https://invalid.local",
            )
        init_database()
        seed_demo_data()

        rows = []
        for scenario in scenarios:
            shop = repository.get_shop(scenario["shop"])
            response = await sales_agent.respond(shop, scenario["message"])
            trace = repository.get_trace(response["conversation_id"])
            tools = [item["tool_name"] for item in trace["tool_calls"]]
            returned_skus = [
                product.get("sku")
                for call in trace["tool_calls"]
                if call["tool_name"] == "search_products"
                for product in call["result"].get("products", [])
            ]
            checks = {
                "tools": all(tool in tools for tool in scenario["expected_tools"]),
                "sku": not scenario.get("expected_sku") or scenario["expected_sku"] in returned_skus,
                "status": not scenario.get("expected_status") or response["status"] == scenario["expected_status"],
            }
            rows.append(
                {
                    "id": scenario["id"],
                    "passed": all(checks.values()),
                    "checks": checks,
                    "actual_tools": tools,
                    "actual_status": response["status"],
                    "model": response["model"],
                }
            )

    database_module.settings = original_db_settings
    agent_module.settings = original_agent_settings
    passed = sum(1 for row in rows if row["passed"])
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "online" if online else "deterministic-offline",
        "total": len(rows),
        "passed": passed,
        "pass_rate": round(passed / len(rows) * 100, 1),
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ShopPilot tool routing and safety workflows.")
    parser.add_argument("--online", action="store_true", help="Use the configured Groq model.")
    parser.add_argument("--output", default="evals/latest_results.json")
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.online))
    output = ROOT / args.output
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['passed']}/{result['total']} passed ({result['pass_rate']}%) [{result['mode']}]")
    for row in result["results"]:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['id']}: {', '.join(row['actual_tools'])}")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
