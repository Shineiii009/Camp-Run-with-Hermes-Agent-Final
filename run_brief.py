"""Run the skill's triage procedure over the real MCP server and emit the
brief's numbers. Used when the MCP tools are not loaded into the live session
(server registered after session start) — same server, same tools, same data.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")
PYTHON = os.path.join(HERE, ".venv", "Scripts", "python.exe")


def unwrap(res):
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    blocks = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
    return blocks[0] if len(blocks) == 1 else blocks


async def main() -> int:
    params = StdioServerParameters(command=PYTHON, args=[SERVER], env=None)
    out = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            out["summary"] = unwrap(await s.call_tool("triage_summary", {"days": 90}))

            for code in ("ALB", "BGC", "ERM"):
                out[f"trace_{code}"] = unwrap(
                    await s.call_tool("trace_complaint_causes", {"branch_code": code, "days": 90})
                )

            out["plan_ALB"] = unwrap(
                await s.call_tool("draft_restock_plan", {"branch_code": "ALB", "days_of_cover": 3})
            )
            out["stock_ORT"] = unwrap(
                await s.call_tool("find_stockouts", {"branch_code": "ORT", "days_of_cover": 0})
            )
            out["stock_KAT"] = unwrap(
                await s.call_tool("find_stockouts", {"branch_code": "KAT", "days_of_cover": 0})
            )
            out["plan_ORT"] = unwrap(
                await s.call_tool("draft_restock_plan", {"branch_code": "ORT", "days_of_cover": 3})
            )
    json.dump(out, open(os.path.join(HERE, "brief.json"), "w", encoding="utf-8"), indent=1)

    print("=== TRIAGE BOARD (all 12) ===")
    for r in out["summary"]:
        print(f"{r['code']:4} pain={r['pain_score']:4} so={r['stockouts_now']:3} risk3d={r['at_risk_3d']:3} "
              f"open={r['open_tickets']:3} hp={r['high_pri_open']:3} age={r['avg_ticket_age_days']:>5} "
              f"unans={r['unanswered_bad_reviews']:3} star={r['avg_bad_rating']} :: {r['verdict']}")

    print("\n=== TRACE ALB (worst) ===")
    print(json.dumps(out["trace_ALB"]["branch_totals"], indent=1))
    print("\n=== TRACE BGC (3rd, zero stockouts?) ===")
    print(json.dumps(out["trace_BGC"]["branch_totals"], indent=1))
    print("\n=== TRACE ERM (most stockouts, few tickets) ===")
    print(json.dumps(out["trace_ERM"]["branch_totals"], indent=1))

    print("\n=== RESTOCK PLAN ALB ===")
    plan = out["plan_ALB"]
    tot = sum(p["weekly_revenue_at_risk"] for p in plan)
    tr = sum(1 for p in plan if p["suggested_source"] == "transfer")
    print(f"{len(plan)} SKUs | {tr} transferable | weekly PHP {tot:,.0f} at risk")
    for p in plan[:8]:
        print(f"  {p['product'][:30]:32} {p['sku']:14} on_hand={p['on_hand']:4} "
              f"PHP{p['weekly_revenue_at_risk']:>8,.0f} qty={p['suggested_qty']:4} "
              f"{p['suggested_source']:15} from={p['transfer_from'] or '-'}")

    print("\n=== PLAN ORT ===")
    p2 = out["plan_ORT"]
    print(f"{len(p2)} SKUs | {sum(1 for p in p2 if p['suggested_source']=='transfer')} transferable | "
          f"weekly PHP {sum(p['weekly_revenue_at_risk'] for p in p2):,.0f} at risk")

    print(f"\n=== STOCKOUTS ORT={len(out['stock_ORT'])} KAT={len(out['stock_KAT'])} ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))