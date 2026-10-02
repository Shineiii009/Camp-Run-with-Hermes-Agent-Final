"""Draft this week's inter-branch transfers for the stockout-driven branches.

READ-ONLY. Builds the proposal only; it writes nothing. The key thing this
checks that a per-branch plan cannot: whether the DONOR branches can supply all
recipients at once, or whether the four branches are all drawing from the same
two donors and over-committing them.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")
PYTHON = os.path.join(HERE, ".venv", "Scripts", "python.exe")

TARGETS = ["ALB", "ORT", "KAT", "ERM"]


def unwrap(res):
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    blocks = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
    return blocks[0] if len(blocks) == 1 else blocks


async def main() -> int:
    params = StdioServerParameters(command=PYTHON, args=[SERVER], env=None)
    plans, traces = {}, {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            for code in TARGETS:
                plans[code] = unwrap(await s.call_tool(
                    "draft_restock_plan", {"branch_code": code, "days_of_cover": 3}))
                traces[code] = unwrap(await s.call_tool(
                    "trace_complaint_causes", {"branch_code": code, "days": 90}))["branch_totals"]

            # Donor capacity AFTER every proposed transfer, checked per SKU.
            donors = {}
            for code in TARGETS:
                for row in plans[code]:
                    src = row["transfer_from"]
                    if not src or row["suggested_source"] != "transfer":
                        continue
                    donors.setdefault(src, []).append((code, row["sku"], row["suggested_qty"]))

            conflicts = []
            for src, draws in donors.items():
                for sku in {d[1] for d in draws}:
                    want = sum(q for c, k, q in draws if k == sku)
                    cap = unwrap(await s.call_tool(
                        "find_transfer_sources", {"sku": sku, "for_branch_code": TARGETS[0]}))
                    spare_here = next((c["transferable"] for c in cap if c["branch"] == src), 0)
                    if want > spare_here:
                        conflicts.append((src, sku, want, spare_here))

    print("=" * 78)
    print("DRAFT — NOTHING WRITTEN. Awaiting approval.")
    print("=" * 78)

    total_lines = 0
    grand = 0
    donor_load = defaultdict(list)
    for code in TARGETS:
        t = traces[code]
        transfer = [r for r in plans[code] if r["suggested_source"] == "transfer"]
        po = [r for r in plans[code] if r["suggested_source"] == "purchase_order"]
        risk = sum(r["weekly_revenue_at_risk"] for r in plans[code])
        grand += risk
        total_lines += len(transfer)
        print(f"\n### {code}  ({t['name']})")
        print(f"    stockouts={t['stockouts_now']}  stock_tickets={t['stock_related_tickets']} "
              f"(chain avg {t['all_branch_avg_missing_item_tickets']}, outlier={t['is_outlier']})")
        print(f"    unanswered_bad={t['unanswered_bad_reviews_any_topic']}  "
              f"unanswered_stock_topic={t['stock_bad_reviews_unanswered']}  avg_bad_rating={t['avg_bad_rating']}")
        print(f"    {len(transfer)} transfers + {len(po)} purchase orders | "
              f"weekly PHP {risk:,.0f} at risk")
        for r in sorted(transfer, key=lambda x: -x["weekly_revenue_at_risk"]):
            donor_load[r["transfer_from"]].append((code, r["sku"], r["suggested_qty"]))
            print(f"      {r['sku']:14} {r['product'][:26]:28} qty={r['suggested_qty']:>4} "
                  f"from {r['transfer_from']:<4} (spare {r['transfer_spare']:.0f})  "
                  f"PHP{r['weekly_revenue_at_risk']:>7,.0f}/wk")
        if po:
            print(f"      -- supplier orders needed (no branch has enough spare):")
            for r in sorted(po, key=lambda x: -x["weekly_revenue_at_risk"]):
                print(f"      {r['sku']:14} {r['product'][:26]:28} qty={r['suggested_qty']:>4} "
                      f"PO {r['supplier']} ({r['promised_lead_time_days']}d)")

    print("\n" + "=" * 78)
    print("DONOR LOAD — the part a per-branch plan hides")
    print("=" * 78)
    for src in sorted(donor_load):
        draws = donor_load[src]
        skus = defaultdict(int)
        for c, k, q in draws:
            skus[k] += q
        print(f"  {src}: supplying {len(draws)} lines across "
              f"{len({c for c,_,_ in draws})} branches, {len(skus)} distinct SKUs")

    print("\nOVER-COMMIT CHECK (same SKU drawn by >1 recipient):")
    if conflicts:
        for src, sku, want, spare in conflicts:
            print(f"  CONFLICT {sku} from {src}: want {want} > spare {spare:.0f}")
    else:
        print("  none — every donor can cover every draw on the same SKU.")

    print(f"\nTOTALS: {total_lines} transfer lines | PHP {grand:,.0f}/week revenue at risk")
    json.dump({"plans": plans, "traces": traces}, open(os.path.join(HERE, "transfers.json"), "w"),
              indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))