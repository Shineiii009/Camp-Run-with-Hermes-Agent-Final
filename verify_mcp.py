"""Boot the real MCP server over stdio and list the tools it exposes.
This is the same code path Hermes uses, so it catches decorator/signature errors
that a stubbed import cannot.
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
    """A FastMCP tool returning a list emits one text block PER ROW, so the
    authoritative payload is structuredContent['result']; fall back to joining
    the text blocks when it is absent."""
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    blocks = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
    if len(blocks) == 1:
        return blocks[0]
    return blocks


async def main() -> int:
    params = StdioServerParameters(command=PYTHON, args=[SERVER], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} TOOLS EXPOSED OVER MCP:\n")
            for t in tools.tools:
                req = t.inputSchema.get("required", [])
                props = list(t.inputSchema.get("properties", {}))
                sig = ", ".join(props)
                print(f"  {t.name}({sig})" + (f"  [required: {req}]" if req else ""))
                print(f"      {(t.description or '').splitlines()[0][:96]}")

            print("\n--- LIVE CALLS ---")
            r = await session.call_tool("triage_summary", {"days": 90})
            rows = unwrap(r)
            print(f"triage_summary -> {len(rows)} branches; top 3:")
            for row in rows[:3]:
                print(f"   {row['code']:4} pain={row['pain_score']:4} stockouts={row['stockouts_now']:3}"
                      f" open={row['open_tickets']:3} unanswered={row['unanswered_bad_reviews']:3}"
                      f" :: {row['verdict']}")

            r = await session.call_tool("find_stockouts", {"branch_code": "ALB", "days_of_cover": 0})
            stock = unwrap(r)
            print(f"\nfind_stockouts(ALB) -> {len(stock)} items")

            r = await session.call_tool("trace_complaint_causes", {"branch_code": "ALB", "days": 90})
            tr = unwrap(r)
            print(f"trace_complaint_causes(ALB) -> outlier={tr['branch_totals']['is_outlier']}, "
                  f"{len(tr['products'])} products")

            r = await session.call_tool("draft_restock_plan", {"branch_code": "ALB", "days_of_cover": 3})
            plan = unwrap(r)
            print(f"draft_restock_plan(ALB) -> {len(plan)} rows, "
                  f"{sum(1 for p in plan if p['suggested_source'] == 'transfer')} transfers")
            for p in plan[:3]:
                print(f"   {p['product'][:28]:30} qty={p['suggested_qty']:4} via {p['suggested_source']}")

            r = await session.call_tool("find_transfer_sources",
                                        {"sku": plan[0]["sku"], "for_branch_code": "ALB"})
            print(f"find_transfer_sources -> {len(unwrap(r))} sources")

            r = await session.call_tool("list_branches", {})
            print(f"list_branches -> {len(unwrap(r))} branches")

            r = await session.call_tool("describe_sandbox", {})
            print(f"describe_sandbox -> {len(unwrap(r))} sections")

    print("\nALL MCP CALLS SUCCEEDED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))