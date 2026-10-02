# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]  # pinned: v1 API (FastMCP)
# ///
"""
LAYER 1 — MCP SERVER (the hands)
Suki Ops — Branch Triage server over the Suki Mart sandbox (data/store.db).

The team idea: stockouts at a handful of branches are the ROOT CAUSE of the
missing-item tickets and angry reviews those branches receive. These tools let
Hermes trace that chain and act on it.

Tools
  describe_sandbox        - orient yourself in the data (exploration helper)
  list_branches           - branch reference
  find_stockouts          - products a branch has run out of / is about to
  find_transfer_sources   - sibling branches that actually hold the stock
  trace_complaint_causes  - join stockouts to the tickets + reviews they caused
  draft_restock_plan      - recommended replenishment, respecting pending POs
  flag_review_for_reply   - action: queue an unreplied bad review
  create_transfer_order   - action: write a branch-to-branch transfer record
  triage_summary         - one-call overview across all 12 branches

Run standalone to check it starts (Ctrl+C to stop):
    uv run mcp-server/server.py

Register with Hermes (absolute path to this file):
    hermes mcp add suki-ops --command uv --args run /ABSOLUTE/PATH/mcp-server/server.py
    # restart Hermes, then: hermes mcp test suki-ops
"""
import os
import sqlite3
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "store.db")

mcp = FastMCP("suki-ops")

# The sandbox's "today". All relative windows in these tools key off this.
SANDBOX_NOW = date(2026, 9, 30)


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Read helper: returns rows as dicts."""
    con = sqlite3.connect(f"file:{os.path.abspath(DB_PATH)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Write helper: returns affected row count. Use for tools that take action.
    Reset the sandbox anytime with `python data/seed.py`."""
    con = sqlite3.connect(os.path.abspath(DB_PATH))
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def _branch_id(code: str) -> int | None:
    row = query("SELECT id FROM branches WHERE code = ?", (code.upper(),))
    return row[0]["id"] if row else None


# --------------------------------------------------------------------------
# Scaffolding — helps the agent explore. Keep or remove.
# --------------------------------------------------------------------------
@mcp.tool()
def describe_sandbox() -> dict:
    """Describe the Suki Mart sandbox: business context, the current date
    inside the data ("sandbox_now"), and every table with its columns and
    row count. Call this first when you need to understand the data."""
    info = {r["key"]: r["value"] for r in query("SELECT key, value FROM sandbox_info")}
    tables = {}
    for t in query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = t["name"]
        cols = [f'{c["name"]} {c["type"]}' for c in query(f"PRAGMA table_info({name})")]
        count = query(f"SELECT COUNT(*) AS n FROM {name}")[0]["n"]
        tables[name] = {"rows": count, "columns": cols}
    return {"info": info, "tables": tables}


@mcp.tool()
def list_branches(city: str | None = None) -> list[dict]:
    """List Suki Mart branches with their code, type, city and whether they
    offer delivery. Optionally filter by city (e.g. "Quezon City")."""
    sql = "SELECT code, name, branch_type, city, area, has_delivery FROM branches"
    if city:
        return query(sql + " WHERE city = ? ORDER BY code", (city,))
    return query(sql + " ORDER BY code")


# --------------------------------------------------------------------------
# Layer 1 of the triage chain: the stockouts themselves
# --------------------------------------------------------------------------
@mcp.tool()
def find_stockouts(branch_code: str, days_of_cover: float = 0.0) -> list[dict]:
    """Products at one branch that are OUT OF STOCK or will run out within
    days_of_cover days at the branch's current sales pace.

    Use this when a branch manager or ops lead asks what is running out, what
    should we restock, or why a branch keeps getting "missing item" complaints.
    days_of_cover=0 returns only items already at zero; try 3.0 to see the
    next three days of risk. Results are ordered by revenue at risk.
    """
    bid = _branch_id(branch_code)
    if bid is None:
        return []
    sql = """
        SELECT b.code AS branch, p.sku, p.name AS product, p.category, p.unit,
               i.on_hand, i.avg_daily_sales,
               ROUND(i.on_hand / NULLIF(i.avg_daily_sales, 0), 2) AS days_of_cover,
               i.reorder_point, i.reorder_qty, i.last_restocked_at,
               ROUND(p.retail_price * i.avg_daily_sales * 7, 0) AS weekly_revenue_at_risk
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        JOIN branches b ON b.id = i.branch_id
        WHERE i.branch_id = ?
          AND p.is_active = 1
          AND i.avg_daily_sales > 0
          AND i.on_hand / NULLIF(i.avg_daily_sales, 0) <= ?
        ORDER BY weekly_revenue_at_risk DESC
    """
    return query(sql, (bid, float(days_of_cover)))


@mcp.tool()
def find_transfer_sources(sku: str, for_branch_code: str) -> list[dict]:
    """OTHER branches that have healthy stock of a given SKU, ranked by how much
    spare they hold above their own reorder point.

    Use this after find_stockouts, to answer "where can we get it from today?"
    without waiting for a supplier. Excludes the branch that needs the stock.
    Matches on product SKU (e.g. "RICE-5KG").
    """
    sql = """
        SELECT b.code AS branch, b.name AS branch_name, b.city,
               i.on_hand, i.reorder_point, i.avg_daily_sales,
               ROUND(i.on_hand / NULLIF(i.avg_daily_sales, 0), 2) AS days_of_cover,
               MAX(i.on_hand - i.reorder_point, 0) AS spare_above_reorder,
               ROUND(i.on_hand - i.reorder_point, 0) AS transferable
        FROM inventory i
        JOIN branches b ON b.id = i.branch_id
        JOIN products p ON p.id = i.product_id
        WHERE p.sku = ?
          AND b.code <> ?
          AND i.on_hand > i.reorder_point
          AND i.avg_daily_sales > 0
          AND i.on_hand / NULLIF(i.avg_daily_sales, 0) > 3
        ORDER BY transferable DESC
    """
    return query(sql, (sku, for_branch_code.upper()))


# --------------------------------------------------------------------------
# Layer 2 of the chain: what the stockouts are costing in complaints
# --------------------------------------------------------------------------
@mcp.tool()
def trace_complaint_causes(branch_code: str, days: int = 90) -> dict:
    """Evidence that a branch's stockouts are actually costing it goodwill:
    branch-level complaint volume alongside per-product attribution.

    Most tickets in this data have no order_id, so they cannot be pinned to a
    specific product. This tool therefore reports BOTH levels honestly:
      * branch totals (reliable) — missing_item/damaged tickets, unresolved
        count, unanswered 1-3 star reviews, and their average rating
      * per-product (partial) — 'tickets_naming_product' counts only those
        linked through order_items, so it under-counts. Never present a
        product's ticket count as the total for that product.

    Use this to justify WHY a restock matters rather than just that stock is
    low. days=90 is about a quarter.
    """
    bid = _branch_id(branch_code)
    if bid is None:
        return {"error": f"unknown branch {branch_code}"}
    since = (SANDBOX_NOW - timedelta(days=days)).isoformat()

    branch = query(
        """
        SELECT b.code, b.name,
               (SELECT COUNT(*) FROM inventory i WHERE i.branch_id = b.id
                  AND i.avg_daily_sales > 0 AND i.on_hand <= 0) AS stockouts_now,
               (SELECT COUNT(*) FROM inventory i WHERE i.branch_id = b.id
                  AND i.avg_daily_sales > 0 AND i.on_hand > 0
                  AND i.on_hand / NULLIF(i.avg_daily_sales,0) <= 3) AS at_risk_3d,
               (SELECT COUNT(*) FROM support_tickets t WHERE t.branch_id = b.id
                  AND t.category IN ('missing_item','damaged_item')
                  AND t.created_at >= :since) AS stock_related_tickets,
               (SELECT COUNT(*) FROM support_tickets t WHERE t.branch_id = b.id
                  AND t.category IN ('missing_item','damaged_item')
                  AND t.status IN ('open','pending') AND t.created_at >= :since) AS still_unresolved,
               (SELECT ROUND(AVG(julianday(:now) - julianday(t.created_at)),0)
                  FROM support_tickets t WHERE t.branch_id = b.id
                  AND t.status IN ('open','pending')) AS avg_unresolved_age_days,
               (SELECT COUNT(*) FROM reviews r WHERE r.branch_id = b.id
                  AND r.rating <= 3 AND r.topic IN ('stock','freshness')
                  AND r.created_at >= :since) AS bad_reviews,
               (SELECT COUNT(*) FROM reviews r WHERE r.branch_id = b.id
                  AND r.rating <= 3 AND r.replied_at IS NULL
                  AND r.topic IN ('stock','freshness')
                  AND r.created_at >= :since) AS stock_bad_reviews_unanswered,
               (SELECT COUNT(*) FROM reviews r WHERE r.branch_id = b.id
                  AND r.rating <= 3 AND r.replied_at IS NULL
                  AND r.created_at >= :since) AS unanswered_bad_reviews_any_topic,
               (SELECT ROUND(AVG(r.rating),2) FROM reviews r WHERE r.branch_id = b.id
                  AND r.rating <= 3 AND r.topic IN ('stock','freshness')
                  AND r.created_at >= :since) AS avg_bad_rating
        FROM branches b WHERE b.id = :bid
        """,
        {"since": since, "bid": bid, "now": f"{SANDBOX_NOW.isoformat()} 21:00:00"},
    )[0]

    products = query(
        """
        SELECT p.sku, p.name AS product, p.category, i.on_hand,
               ROUND(i.on_hand / NULLIF(i.avg_daily_sales, 0), 2) AS days_of_cover,
               ROUND(p.retail_price * i.avg_daily_sales * 7, 0) AS weekly_revenue_at_risk,
               (SELECT COUNT(*) FROM support_tickets t
                  JOIN order_items oi ON oi.order_id = t.order_id
                 WHERE t.branch_id = i.branch_id AND oi.product_id = i.product_id
                   AND t.category IN ('missing_item','damaged_item')
                   AND t.created_at >= ?) AS tickets_naming_product,
               (SELECT COUNT(*) FROM reviews r
                  JOIN order_items oi ON oi.order_id = r.order_id
                 WHERE r.branch_id = i.branch_id AND oi.product_id = i.product_id
                   AND r.rating <= 3 AND r.topic IN ('stock','freshness')
                   AND r.created_at >= ?) AS bad_reviews_naming_product
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        WHERE i.branch_id = ?
          AND p.is_active = 1
          AND i.avg_daily_sales > 0
          AND i.on_hand / NULLIF(i.avg_daily_sales, 0) <= 2
        ORDER BY tickets_naming_product DESC, weekly_revenue_at_risk DESC
        """,
        (since, since, bid),
    )

    # Is this branch an outlier, or does everyone look like this?
    peer = query(
        """
        SELECT ROUND(AVG(x.c), 2) AS avg_missing_item_tickets_all_branches
                FROM (SELECT (SELECT COUNT(*) FROM support_tickets t2
                               WHERE t2.branch_id = b2.id AND t2.category = 'missing_item'
                                 AND t2.created_at >= ?) AS c
                      FROM branches b2) x
        """,
        (since,),
    )[0]["avg_missing_item_tickets_all_branches"]

    branch["all_branch_avg_missing_item_tickets"] = peer
    branch["is_outlier"] = bool(branch["stock_related_tickets"] > (peer or 0) * 1.5)
    return {
        "branch_totals": branch,
        "attribution_note": (
            "tickets_naming_product counts only tickets joined through order_items "
            "(a minority of tickets have an order_id) — treat it as a lower bound."
        ),
        "products": products,
    }


# --------------------------------------------------------------------------
# Layer 3: the recommendation, respecting POs already in flight
# --------------------------------------------------------------------------
@mcp.tool()
def draft_restock_plan(branch_code: str, days_of_cover: float = 3.0) -> list[dict]:
    """A ranked restock recommendation for one branch that ALREADY accounts for
    purchase orders already in flight and points to a transfer source when a
    sibling branch has spare stock.

    Each row carries a suggested source ('transfer' vs 'purchase_order'), the
    quantity, and the reason. Do not recommend ordering something that already
    has a pending or in-transit PO — this tool excludes those for you.
    Use this to answer "what should we restock at ALB, and where from?"
    """
    bid = _branch_id(branch_code)
    if bid is None:
        return []
    # No LATERAL in SQLite: the transfer source is a correlated subquery.
    sql = """
        SELECT p.sku, p.name AS product, p.category,
               i.on_hand, i.reorder_qty, i.avg_daily_sales,
                              ROUND(i.on_hand / NULLIF(i.avg_daily_sales, 0), 2) AS days_of_cover,
                              ROUND(p.retail_price * i.avg_daily_sales * 7, 0) AS weekly_revenue_at_risk,
               (SELECT COUNT(*) FROM purchase_orders po
                 WHERE po.branch_id = i.branch_id AND po.product_id = i.product_id
                   AND po.status IN ('pending', 'in_transit', 'partially_received')) AS pos_in_flight,
               (SELECT MIN(po.expected_at) FROM purchase_orders po
                 WHERE po.branch_id = i.branch_id AND po.product_id = i.product_id
                   AND po.status IN ('pending', 'in_transit', 'partially_received')) AS next_expected,
               s.name AS supplier, s.promised_lead_time_days,
               (SELECT b2.code FROM inventory i2
                  JOIN branches b2 ON b2.id = i2.branch_id
                 WHERE i2.product_id = i.product_id
                   AND i2.branch_id <> i.branch_id
                   AND i2.on_hand > i2.reorder_point
                   AND i2.avg_daily_sales > 0
                   AND i2.on_hand / NULLIF(i2.avg_daily_sales, 0) > 3
                 ORDER BY (i2.on_hand - i2.reorder_point) DESC LIMIT 1) AS transfer_from,
               (SELECT MAX(i2.on_hand - i2.reorder_point) FROM inventory i2
                 WHERE i2.product_id = i.product_id
                   AND i2.branch_id <> i.branch_id
                   AND i2.on_hand > i2.reorder_point
                   AND i2.avg_daily_sales > 0
                   AND i2.on_hand / NULLIF(i2.avg_daily_sales, 0) > 3) AS transfer_spare
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        LEFT JOIN suppliers s ON s.id = p.supplier_id
        WHERE i.branch_id = ?
          AND p.is_active = 1
          AND i.avg_daily_sales > 0
          AND i.on_hand / NULLIF(i.avg_daily_sales, 0) <= ?
          AND NOT EXISTS (
              SELECT 1 FROM purchase_orders po
              WHERE po.branch_id = i.branch_id AND po.product_id = i.product_id
                AND po.status IN ('pending', 'in_transit', 'partially_received')
          )
        ORDER BY i.on_hand ASC, (i.avg_daily_sales * p.retail_price) DESC
    """
    rows = query(sql, (bid, float(days_of_cover)))
    for r in rows:
        need = max(r["reorder_qty"] or 0, round((r["avg_daily_sales"] or 0) * 14))
        r["suggested_qty"] = need
        if r["transfer_from"] and r["transfer_spare"] and r["transfer_spare"] >= need:
            r["suggested_source"] = "transfer"
            r["reason"] = f"out of stock; {r['transfer_from']} has {int(r['transfer_spare'])} spare — move today, no supplier wait"
        else:
            r["suggested_source"] = "purchase_order"
            r["reason"] = f"out of stock or under {days_of_cover}d cover; supplier lead time {r['promised_lead_time_days']}d"
    return rows


# --------------------------------------------------------------------------
# Action tools — these write. Reset the sandbox with `python data/seed.py`.
# --------------------------------------------------------------------------
@mcp.tool()
def flag_review_for_reply(review_id: int, note: str = "") -> dict:
    """ACTION: mark a customer review as replied to, so it drops out of the
    'unanswered complaints' queue. Pass a short note describing the reply you
    sent or plan to send.

    Use after triage_summary identifies angry reviews nobody has answered.
    Always confirm with the user before calling this — it changes data.
    """
    review = query("SELECT id, branch_id, rating, topic FROM reviews WHERE id = ?", (review_id,))
    if not review:
        return {"ok": False, "error": f"review {review_id} not found"}
    n = execute(
        "UPDATE reviews SET replied_at = ?, reply_text = ? WHERE id = ?",
        (f"{SANDBOX_NOW.isoformat()} 12:00:00",
         "[QUEUED] " + (note or "Reply pending, branch is reviewing this."), review_id),
    )
    return {"ok": n > 0, "review_id": review_id, "rows_updated": n}


@mcp.tool()
def create_transfer_order(to_branch_code: str, sku: str, quantity: int, from_branch_code: str | None = None) -> dict:
    """ACTION: record a branch-to-branch stock transfer as a purchase_order row
    with status 'pending' and a note naming the source branch. If from_branch_code
    is omitted, the tool picks the branch with the most spare stock.

    Use to action a restock recommendation. Returns the created PO number.
    Always confirm with the user before calling this — it changes data.
    """
    if quantity <= 0:
        return {"ok": False, "error": "quantity must be greater than 0"}
    to_bid = _branch_id(to_branch_code)
    if to_bid is None:
        return {"ok": False, "error": f"unknown branch {to_branch_code}"}
    prod = query("SELECT id, name, supplier_id FROM products WHERE sku = ?", (sku,))
    if not prod:
        return {"ok": False, "error": f"unknown sku {sku}"}
    pid = prod[0]["id"]

    if from_branch_code:
        src = _branch_id(from_branch_code)
        if src is None:
            return {"ok": False, "error": f"unknown branch {from_branch_code}"}
    else:
        row = query(
            """SELECT branch_id FROM inventory
               WHERE product_id = ? AND branch_id <> ?
                 AND on_hand > reorder_point AND avg_daily_sales > 0
                 AND on_hand / NULLIF(avg_daily_sales,0) > 3
               ORDER BY (on_hand - reorder_point) DESC LIMIT 1""",
            (pid, to_bid),
        )
        if not row:
            return {"ok": False, "error": "no branch has spare stock of that SKU"}
        src = row[0]["branch_id"]

    if src == to_bid:
        return {"ok": False, "error": "source and destination are the same branch"}
    stock = query("SELECT on_hand, reorder_point FROM inventory WHERE branch_id = ? AND product_id = ?",
                  (src, pid))
    spare = (stock[0]["on_hand"] - stock[0]["reorder_point"]) if stock else 0
    if quantity > spare:
        return {"ok": False, "error": f"source only has {max(spare, 0)} spare above its reorder point"}

    src_code = query("SELECT code FROM branches WHERE id = ?", (src,))[0]["code"]
    unit_cost = query("SELECT cost_price FROM products WHERE id = ?", (pid,))[0]["cost_price"]
    n = (len(query("SELECT id FROM purchase_orders")) + 1)
    po_number = f"XFER-{SANDBOX_NOW.strftime('%y%m%d')}-{n:04d}"
    rows = execute(
        """INSERT INTO purchase_orders
           (po_number, supplier_id, branch_id, product_id, quantity, unit_cost,
            status, ordered_at, expected_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (po_number, prod[0]["supplier_id"], to_bid, pid, quantity, unit_cost,
         f"{SANDBOX_NOW.isoformat()} 09:00:00", f"{SANDBOX_NOW.isoformat()} 18:00:00",
         f"Inter-branch transfer from {src_code}"),
    )
    return {"ok": rows > 0, "po_number": po_number, "from": src_code,
            "to": to_branch_code.upper(), "sku": sku, "quantity": quantity,
            "product": prod[0]["name"]}


# --------------------------------------------------------------------------
# The one-call overview that drives the GUI
# --------------------------------------------------------------------------
@mcp.tool()
def triage_summary(days: int = 90) -> list[dict]:
    """A single ranked overview of all 12 branches by how much customer pain
    their stockouts are causing. Use this FIRST when someone asks for a daily
    ops brief, a triage board, or 'which branches need help today' — it saves
    calling every other tool per branch.

    Combines: stockout count, unresolved tickets, unanswered bad reviews,
    and the weighted 'pain score' used to rank them.
    """
    since = (SANDBOX_NOW - timedelta(days=days)).isoformat()
    sql = """
        SELECT b.code, b.name, b.city, b.branch_type,
               (SELECT COUNT(*) FROM inventory i
                 WHERE i.branch_id = b.id AND i.avg_daily_sales > 0
                   AND i.on_hand / NULLIF(i.avg_daily_sales,0) <= 0) AS stockouts_now,
               (SELECT COUNT(*) FROM inventory i
                 WHERE i.branch_id = b.id AND i.avg_daily_sales > 0
                   AND i.on_hand > 0
                   AND i.on_hand / NULLIF(i.avg_daily_sales,0) <= 3) AS at_risk_3d,
               (SELECT COUNT(*) FROM support_tickets t
                 WHERE t.branch_id = b.id AND t.status IN ('open','pending')
                   AND t.created_at >= ?) AS open_tickets,
               (SELECT COUNT(*) FROM support_tickets t
                 WHERE t.branch_id = b.id AND t.status IN ('open','pending')
                   AND t.priority IN ('high', 'urgent') AND t.created_at >= ?) AS high_pri_open,
               (SELECT ROUND(AVG(julianday(?) - julianday(t.created_at)),0)
                  FROM support_tickets t
                 WHERE t.branch_id = b.id AND t.status IN ('open','pending')
                   AND t.created_at >= ?) AS avg_ticket_age_days,
               (SELECT COUNT(*) FROM reviews r
                 WHERE r.branch_id = b.id AND r.replied_at IS NULL
                   AND r.rating <= 3 AND r.created_at >= ?) AS unanswered_bad_reviews,
               (SELECT ROUND(AVG(r.rating),2) FROM reviews r
                 WHERE r.branch_id = b.id AND r.rating <= 3
                   AND r.created_at >= ?) AS avg_bad_rating,
               (SELECT COUNT(*) FROM orders o
                 WHERE o.branch_id = b.id AND o.created_at >= ?
                   AND o.channel = 'delivery') AS delivery_orders
        FROM branches b
        ORDER BY b.code
    """
    rows = query(sql, (since, since, f"{SANDBOX_NOW.isoformat()} 21:00:00",
                       since, since, since, since))
    for r in rows:
        # Weighted so a branch with real stockouts AND real complaints outranks one with noise.
        r["pain_score"] = (
            r["stockouts_now"] * 5
            + r["at_risk_3d"] * 2
            + r["open_tickets"] * 2
            + r["high_pri_open"] * 4
            + r["unanswered_bad_reviews"] * 2
        )
        r["verdict"] = (
            "stockout-driven" if r["stockouts_now"] >= 5
            else "complaints-without-stockouts" if r["open_tickets"] >= 15
            else "healthy"
        )
    return sorted(rows, key=lambda r: -r["pain_score"])


if __name__ == "__main__":
    mcp.run()