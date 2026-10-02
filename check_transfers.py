"""Corrected over-commit check.

The first pass passed for_branch_code=ALB, which EXCLUDES ALB from the donor
list — so any draw from ALB reported spare=0 and looked like a conflict. Here the
lookups are per-recipient and the true ALB stock is read straight from inventory.
"""
import asyncio
import json
import os
import sqlite3
import sys
from collections import defaultdict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp-server", "server.py")
PYTHON = os.path.join(HERE, ".venv", "Scripts", "python.exe")
DB = os.path.join(HERE, "data", "store.db")
TARGETS = ["ALB", "ORT", "KAT", "ERM"]


def unwrap(res):
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    blocks = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
    return blocks[0] if len(blocks) == 1 else blocks


async def main() -> int:
    params = StdioServerParameters(command=PYTHON, args=[SERVER], env=None)
    plans = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            for code in TARGETS:
                plans[code] = unwrap(await s.call_tool(
                    "draft_restock_plan", {"branch_code": code, "days_of_cover": 3}))

    # Ground truth per (donor, sku): on_hand - reorder_point
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda sql, p=(): [dict(r) for r in con.execute(sql, p)]
    true_spare = {(r["branch"], r["sku"]): r["spare"]
                  for r in q("""SELECT b.code branch, p.sku, MAX(i.on_hand - i.reorder_point,0) spare
                                FROM inventory i JOIN branches b ON b.id=i.branch_id
                                JOIN products p ON p.id=i.product_id
                                GROUP BY b.code, p.sku""")}

    print("=" * 78)
    print("OVER-COMMIT CHECK (corrected): cumulative draws per donor+SKU")
    print("=" * 78)
    draw = defaultdict(lambda: defaultdict(int))
    for code in TARGETS:
        for r in plans[code]:
            if r["suggested_source"] == "transfer" and r["transfer_from"]:
                draw[r["transfer_from"]][r["sku"]] += r["suggested_qty"]

    bad = []
    for donor in sorted(draw):
        for sku, want in sorted(draw[donor].items()):
            spare = true_spare.get((donor, sku), 0)
            flag = "CONFLICT" if want > spare else ("TIGHT" if want > spare * 0.8 else "ok")
            if want > spare:
                bad.append((donor, sku, want, spare))
            elif flag == "TIGHT":
                print(f"  TIGHT    {donor} {sku}: draw {want} vs spare {spare} ({100*want//spare}%)")
    print()
    if bad:
        for donor, sku, want, spare in bad:
            print(f"  CONFLICT {donor} {sku}: draw {want} > spare {spare}")
    else:
        print("  no true over-commit: every donor covers its cumulative draw.")

    print("\n" + "=" * 78)
    print("SELF-FUNDING CHECK — ALB both receives and supplies")
    print("=" * 78)
    alb_out = [(c, r["sku"], r["suggested_qty"])
               for c in TARGETS for r in plans[c]
               if r["transfer_from"] == "ALB" and r["suggested_source"] == "transfer"]
    alb_in = sum(r["suggested_qty"] for r in plans["ALB"]
                 if r["suggested_source"] == "transfer")
    print(f"  ALB receives {alb_in} units across {len([r for r in plans['ALB'] if r['suggested_source']=='transfer'])} lines")
    for c, sku, qty in alb_out:
        print(f"  ALB supplies {qty} of {sku} -> {c}  (true spare now: {true_spare.get(('ALB', sku), 0)} pre-draft)")
    if alb_out:
        print("\n  -> ALB is a NET DONOR while itself being restocked. Sequencing matters:")
        print("     ALB's own inbound transfers should land BEFORE it ships to KAT/ERM.")

    print("\n" + "=" * 78)
    print("THIN-DONOR CHECK — donors left with <3 days cover after giving")
    print("=" * 78)
    con2 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con2.row_factory = sqlite3.Row
    for donor in sorted(draw):
        for sku, want in sorted(draw[donor].items()):
            r = con2.execute("""SELECT i.on_hand, i.reorder_point, i.avg_daily_sales, b.code, p.sku
                                FROM inventory i JOIN branches b ON b.id=i.branch_id
                                JOIN products p ON p.id=i.product_id
                                WHERE b.code=? AND p.sku=?""", (donor, sku)).fetchone()
            if not r:
                continue
            after = r["on_hand"] - want
            cover = after / r["avg_daily_sales"] if r["avg_daily_sales"] else 99
            if cover < 3:
                print(f"  {donor} {sku}: {r['on_hand']} -> {after} units, "
                      f"{cover:.1f}d cover (below 3d threshold)")
    con2.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))