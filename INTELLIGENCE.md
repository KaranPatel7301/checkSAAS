# checkSAAS 2.0: Acquisition Intelligence

The existing GitHub Actions workflow remains the scheduler. No paid AI API and no ChatGPT API credentials are required.

## What changed

- Every newly surfaced listing receives **rule-based acquisition and operator-for-equity scores**. Scores only reflect evidence that the marketplace actually exposes. Never interpret the score as verified diligence.
- All marketplace results (including ones already in `seen.json`) contribute to `deal_history.json`. The history captures the first/last time we observed a listing and actual asking-price changes.
- Email notifications now include new listings sorted by acquisition opportunity and alerts for price reductions.
- Reddit discovery now looks for founder bottlenecks and requests for growth partners in addition to offers for sale.
- An optional **local stdio MCP server** exposes `search_deals`, `get_deal_history` and `recent_price_drops` over the stored history. This isn't a hosted ChatGPT connector.

## Scheduled operation

The existing `.github/workflows/scout.yml` runs three times daily at 11:00, 17:00 and 23:00 UTC and writes both `seen.json` and `deal_history.json` back to the private repository. This schedule is UTC; observed Eastern clock times shift seasonally.

GitHub Actions Python jobs incur **no OpenAI API costs**. GitHub plan usage/limits still apply. The optional MCP tools likewise make no OpenAI API calls.

## Local use

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
SAAS_SCOUT_HISTORY=deal_history.json python saas_scout.py
```

Local MCP server (for a local MCP-capable client):

```bash
pip install 'mcp>=1.0,<2'
SAAS_SCOUT_HISTORY=deal_history.json python mcp_server.py
```

A private repository keeps marketplace lead history confidential. Do not commit Flippa session cookies or secrets; prefer official provider integration or permitted public data. `FLIPPA_COOKIE` remains supported by the legacy scanner but should not be used to circumvent a block.

## Scoring interpretation

Scores are **preliminary ranking signals**, not proof of quality. Unknown profit, unverified revenue, organic acquisition, churn, source-of-truth financials and seller workload require manual diligence. Revenue cannot substitute for net profit when estimating multiples.

Important diligence before offers: request read-only payment processor exports, cohort churn, traffic source breakdown, bank statements, support workload, code access, IP ownership, platform/API dependencies and seller handover terms.

## ChatGPT subscription and MCP

Your ChatGPT subscription cannot supply API credit to scheduled Python jobs. ChatGPT can help reason about deal data interactively via the existing GitHub connection. A custom ChatGPT MCP connector may require a remotely hosted MCP server and supported account/app configuration; this repo supplies a **local** MCP implementation only. Do not expose the local server publicly without authentication.

## Limitations

- Marketplace HTML/JSON layouts and terms may change. `0` fetched is not evidence that no deals exist.
- Historical price drops will only appear once a listing has at least two **different, valid asking prices** in separate observations.
- `deal_history.json` is initially populated when the modified scanner actually runs on `main`.
- The score is heuristic, not a financial model, and cannot infer organic growth, actual churn or margins.
- Operator-for-equity candidates require thoughtful manual outreach and confirmation that the founder wants a partner.
