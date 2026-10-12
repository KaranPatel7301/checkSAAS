"""Rule-based, zero-API-cost SaaS acquisition intelligence."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path

EQUITY_SIGNALS = ("plateau", "stagnat", "stuck at", "can't grow", "cannot grow",
                  "churn", "onboarding", "pricing", "growth partner", "cofounder",
                  "co-founder", "looking for partner", "need help growing")
BAD_SIGNALS = ("ai girlfriend", "ai boyfriend", "novelty", "meme generator",
               "horoscope", "astrology", "dating app", "one-time purchase")
DEFAULT_HISTORY = "deal_history.json"


def number(value):
    """Only numeric values are trusted; 0 and missing mean unknown for asking prices."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None


def evaluate(deal: dict) -> dict:
    """Return cautious acquisition + partnership scores, with explanatory evidence."""
    text = (str(deal.get("title") or "") + " " + str(deal.get("summary") or "")).lower()
    price = number(deal.get("price"))
    profit = number(deal.get("profit_monthly"))
    revenue = number(deal.get("revenue_monthly"))
    if revenue is None and number(deal.get("annual_revenue")) is not None:
        revenue = number(deal["annual_revenue"]) / 12
    price = price if price and price > 0 else None
    profit = profit if profit and profit > 0 else None
    revenue = revenue if revenue and revenue > 0 else None
    multiple = price / (12 * profit) if price and profit else None
    payback = price / profit if price and profit else None
    return_yield = (12 * profit / price) if price and profit else None
    notes = []
    score = 0
    # Unknowns don't get assumed-positive scores.
    if "saas" in text or deal.get("source") not in ("reddit", None) and not str(deal.get("source")).startswith("reddit"):
        score += 12
    else:
        notes.append("Subscription model unverified")
    if profit is not None:
        score += 20 if profit >= 3000 else 12 if profit >= 2000 else 0
        if profit < 2000:
            notes.append("Below minimum monthly profit")
    else:
        notes.append("Profit not verified")
    if price is not None:
        score += 10 if price <= 150000 else 5 if price <= 250000 else 0
        if price > 250000:
            notes.append("Over asking-price threshold")
    else:
        notes.append("Asking price unknown")
    if multiple is not None:
        score += 25 if multiple <= 1.5 else 18 if multiple <= 2.5 else 0
        if multiple > 2.5:
            notes.append("Above 2.5x annual profit")
    else:
        notes.append("Profit multiple unavailable")
    age = number(deal.get("business_age_months"))
    if age is not None:
        score += 10 if age >= 24 else 5 if age >= 12 else 0
        if age < 12:
            notes.append("Less than 12 months of operating history")
    else:
        notes.append("Business age unverified")
    if deal.get("verified_revenue"):
        score += 10
    else:
        notes.append("Revenue verification required")
    if not any(s in text for s in BAD_SIGNALS):
        score += 5
    else:
        notes.append("Possibly novelty/structural-churn market")
    # No credit for churn, organic traffic, or product simplicity without evidence.
    notes.append("Verify organic acquisition, cohort churn, owner hours, and codebase")
    equity_signals = [s for s in EQUITY_SIGNALS if s in text]
    operator = min(100, 15 * len(equity_signals) + (20 if revenue else 0) +
                   (15 if profit else 0) + (10 if price is None else 0))
    if equity_signals:
        notes.append("Potential operator constraint: " + ", ".join(equity_signals[:3]))
    confidence = "medium" if profit and price else "low"
    return {"acquisition_score": min(100, score), "operator_score": operator,
            "confidence": confidence, "profit_multiple": multiple,
            "payback_months": payback, "annual_cash_yield": return_yield,
            "flags": notes, "track": "operator" if operator >= 45 and operator > score else "acquisition"}


def load_history(path: str | os.PathLike | None = None) -> dict:
    file = Path(path or os.environ.get("SAAS_SCOUT_HISTORY", DEFAULT_HISTORY))
    if not file.exists():
        return {}
    try:
        value = json.loads(file.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"Invalid deal history: {file}") from None


def update_history(deals: list[dict], history: dict, today: str | None = None):
    """Capture price movements even for already-seen listings. Returns price-drop alerts."""
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    drops = []
    for deal in deals:
        ident = str(deal.get("id") or "")
        if not ident:
            continue
        record = history.setdefault(ident, {"first_seen": today, "prices": []})
        record["last_seen"] = today
        record["title"] = str(deal.get("title") or "")[:200]
        record["url"] = str(deal.get("url") or "")
        price = number(deal.get("price"))
        if price is None or price == 0:
            continue
        prices = record.setdefault("prices", [])
        previous = prices[-1]["amount"] if prices else None
        if previous is None or price != previous:
            prices.append({"date": today, "amount": price})
        if previous is not None and price < previous:
            drops.append({**deal, "old_price": previous, "new_price": price,
                          "discount_pct": round((previous - price) / previous * 100, 1)})
    return drops


def save_history(history: dict, path: str | os.PathLike | None = None):
    file = Path(path or os.environ.get("SAAS_SCOUT_HISTORY", DEFAULT_HISTORY))
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(history, indent=2, sort_keys=True))
    tmp.replace(file)


def build_intelligence_digest(deals: list[dict], price_drops: list[dict]) -> str:
    ranked = sorted(deals, key=lambda d: evaluate(d)["acquisition_score"], reverse=True)
    lines = ["ACQUISITION INTELLIGENCE — rule-based estimates, NOT verified due diligence", ""]
    for deal in ranked:
        v = evaluate(deal)
        lines.extend([f"{deal.get('title', 'Untitled')} [{deal.get('source', '?')}]",
            f"  Acquisition {v['acquisition_score']}/100 | Operator {v['operator_score']}/100 | confidence {v['confidence']}",
            f"  Profit multiple: {v['profit_multiple']:.2f}x | Payback: {v['payback_months']:.1f} months" if v["profit_multiple"] is not None else "  Profit multiple/payback: unknown",
            "  Check: " + "; ".join(v["flags"][:5]), f"  {deal.get('url', '')}", ""])
    if price_drops:
        lines.extend(["PRICE REDUCTIONS", ""])
        for d in price_drops:
            lines.append(f"{d.get('title', 'Untitled')}: ${d['old_price']:,.0f} → ${d['new_price']:,.0f} (-{d['discount_pct']}%) {d.get('url', '')}")
    return "\n".join(lines)
