"""Optional local MCP server. Install with: pip install 'mcp>=1.0,<2'. Run: python mcp_server.py.
The scheduled GitHub workflow NEVER imports this module or makes model API calls.
A local stdio MCP server is not a remotely hosted ChatGPT connector.
"""
import json
import os
from acquisition_intelligence import load_history

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit("Install optional MCP dependencies: pip install 'mcp>=1.0,<2'") from exc

mcp = FastMCP("checkSAAS")


@mcp.tool()
def search_deals(keyword: str = "", max_results: int = 20) -> str:
    """Search recorded SaaS deals by name, with asking prices and first/last seen dates."""
    query = keyword.casefold().strip()
    data = load_history()
    matches = [
        {"id": deal_id, **row} for deal_id, row in data.items()
        if query in str(row.get("title", "")).casefold() or query in deal_id.casefold()
    ]
    matches.sort(key=lambda row: row.get("last_seen", ""), reverse=True)
    return json.dumps(matches[:max(1, min(max_results, 100))])


@mcp.tool()
def get_deal_history(deal_id: str) -> str:
    """Inspect recorded asking-price movements for a specific marketplace listing."""
    return json.dumps(load_history().get(deal_id, {"error": "Deal not found"}))


@mcp.tool()
def recent_price_drops(max_results: int = 20) -> str:
    """Find listings whose latest recorded asking price is lower than the previous price."""
    rows = []
    for deal_id, record in load_history().items():
        prices = record.get("prices", [])
        if len(prices) >= 2 and prices[-1]["amount"] < prices[-2]["amount"]:
            rows.append({"id": deal_id, "title": record.get("title"),
                         "url": record.get("url"), "from": prices[-2]["amount"],
                         "to": prices[-1]["amount"], "date": prices[-1]["date"]})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return json.dumps(rows[:max(1, min(max_results, 100))])


if __name__ == "__main__":
    mcp.run()
