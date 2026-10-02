---
name: suki-branch-triage
description: >-
  Use when someone at Suki Mart asks for a daily ops or triage brief, asks which
  branches need help today, asks why a branch keeps getting "missing item"
  complaints or bad reviews, or asks what to restock and whether to transfer
  stock or order it from a supplier. Also use for "morning brief", "triage
  board", "which branches are in trouble", "what's running out", "who's angry at
  us", "stockout", "out of stock", "restock", "branch transfer", "unanswered
  reviews". Covers the Suki Mart 12-branch grocery sandbox.
---

# Suki Branch Triage

> **LAYER 2 — SKILL (the playbook).** The team's thesis, stated once:
> **a few branches are running out of stock, and that is what is generating the
> missing-item tickets and the angry reviews.** Fixing stock at the right branch
> is cheaper than answering complaints one at a time.

The sandbox's "today" is **2026-09-30**. Use it for "this week", "last 30 days",
"overdue" — never the real current date.

## When to use this skill

- A branch manager, ops lead, or CSR supervisor asks for a brief, a triage board,
  or "what should I do first today".
- Someone asks why a branch is receiving complaints.
- Someone asks what to restock, or whether to transfer vs. order.

## Tools this skill uses

Server name is `suki-ops`, so tools appear as `mcp_suki_ops_<tool>`.

1. `mcp_suki_ops_triage_summary` — **start here.** One call ranks all 12 branches
   by `pain_score` and labels each `stockout-driven` / `complaints-without-stockouts` /
   `healthy`. Replaces 12 separate per-branch calls.
2. `mcp_suki_ops_trace_complaint_causes` — for the worst branch: branch totals
   plus per-product attribution, and `is_outlier` vs. other branches.
3. `mcp_suki_ops_draft_restock_plan` — the ranked fix. Already excludes items
   with a PO in flight and already picks transfer-vs-supplier with a reason.
4. `mcp_suki_ops_find_transfer_sources` — only if the plan's `transfer_from` is
   null or its `transfer_spare` looks too thin to cover `suggested_qty`.
5. `mcp_suki_ops_find_stockouts` — for a narrow "just what's out" question.
6. `mcp_suki_ops_create_transfer_order` — **ACTION** (writes).
7. `mcp_suki_ops_flag_review_for_reply` — **ACTION** (writes).
8. `mcp_suki_ops_describe_sandbox` / `list_branches` — only if the data model is
   unclear or a branch code is ambiguous.

## Procedure

1. **Scope it.** If no branch is named, run `triage_summary` and treat the whole
   chain as network-wide. If a branch is named, you may skip straight to step 3,
   but say in your answer how that branch ranks — a manager wants to know if they
   are the worst in the chain or not.

2. **Triage.** Call `triage_summary`. Take the top 2–3 branches by `pain_score`.
   Note each one's `verdict`.

3. **Prove the cause.** For the worst branch call `trace_complaint_causes`.
   Report `stockouts_now`, `stock_related_tickets`, `still_unresolved`,
   `bad_reviews_unanswered`, `avg_bad_rating`, and `is_outlier`. If
   `is_outlier` is false, **say so** — a branch can have many complaints and no
   stockout problem, and a transfer would be the wrong recommendation. Do not
   present the stockout theory as fact where the data does not support it.

4. **Recommend the fix.** Call `draft_restock_plan` for that branch. Present at
   most the top 8 rows. Keep the tool's `suggested_source` and `reason` — the
   transfer-vs-supplier split is the interesting part, because a transfer fixes
   it today while a supplier order takes 2–5 days.

5. **Act only with permission.** `create_transfer_order` and
   `flag_review_for_reply` write to the database. Propose them as a numbered list
   of what you would do, and **wait for explicit confirmation** before calling.
   Never batch-write on your own initiative.

6. **Close with the cost.** State the weekly revenue at risk and the count of
   unanswered bad reviews this would stop. That number is the pitch.

## Output format

- **One-line headline:** the single worst branch and the number, e.g.
  "Alabang is losing customers to stockouts — 11 items at zero, 47 unanswered
  1–3★ reviews (avg 2.1), while BGC has 708 units of the same stock sitting idle."
- **A table:** `branch | stockouts | open tickets | unanswered bad reviews | avg rating | verdict`
- **A second table for the worst branch only:**
  `product | sku | on_hand | weekly ₱ at risk | source | from | qty | reason`
- **Max 3 next actions**, each with a number from the data behind it, each
  flagged as needing approval.
- **One caveat line** if the data does not fully support the thesis for a branch.

Keep the whole answer under ~400 words. This is a brief, not a report.

## Pitfalls

- **Do not recommend ordering something with a PO already in flight.**
  `draft_restock_plan` already filters these; if you call `find_stockouts`
  instead and see `pos_in_flight`, respect it.
- **Do not treat per-product ticket counts as totals.** Only ~950 of 1,650
  tickets have an `order_id`, so `tickets_naming_product` is a **lower bound**.
  Use `branch_totals.stock_related_tickets` for the real number.
- **Do not blame stockouts for every angry branch.** BGC, MKT, PQE and CUB have
  meaningful complaints with zero or near-zero stockouts. BGC is `stockouts=0`
  yet ranks 3rd on pain — its problem is something else (delivery/last-mile),
  and a restock recommendation there would be visibly wrong to a judge.
- **ERM is an edge case:** 14 stockouts, the most in the chain, but almost no
  tickets. Do not let `pain_score` alone decide the narrative — mention ERM's
  stockouts as an emerging risk rather than an active crisis.
- **Branch codes are 3 letters** (ALB, BGC, CUB, ERM, KAT, KPT, MAN, MKN, MKT,
  ORT, PQE, TMR). Uppercase them; the tools expect the code, not the name.
- **Unknown codes return empty, not an error.** If a tool returns `[]` or
  `{"error": "unknown branch"}`, check the code against `list_branches` rather
  than retrying.
- Data can be reset with `python data/seed.py` — but it needs the DB unlocked
  (close any open connection first, or the delete fails on Windows).

## Rendering the triage board (the desktop plugin)

If the `suki-triage` desktop plugin is installed, it registers a transcript
directive named **`suki-triage`**. Emit it ALONE on its own line to render the
branch board inline in your reply instead of a plain table. Use it for the
step-2 triage result — it is the visual centrepiece of the demo.

Format (attributes are pipe-separated rows, comma-separated fields):

::suki-triage{headline="Alabang is losing customers to stockouts" rows="ALB,11,22,47,2.13,stockout-driven|ORT,11,13,38,1.88,stockout-driven" note="14 of17 items can transfer today"}

Field order per row is: `code, stockouts, open_tickets, unanswered_bad_reviews,
avg_rating, verdict`. Verdict must be exactly one of `stockout-driven`,
`complaints-without-stockouts`, or `healthy`.

Rules:
- One directive line, alone on its line, with no leading text.
- Always write the plain-text table as well — the directive is an enhancement,
  and a judge reading a transcript export should still see the numbers.
- Only top 5 rows; the pane is narrow.
