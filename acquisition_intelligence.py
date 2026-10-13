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
    # Attractive target is a negotiation ceiling, not a prediction of seller acceptance.
    # 1.5x–2.5x profit range is intentionally explicit; diligence may justify less.
    target_low = round(12 * profit * 1.5) if profit else None
    target_high = round(12 * profit * 2.5) if profit else None
    required_discount = round(max(0, (price - target_high) / price * 100), 1) if price and target_high else None
    negotiation_candidate = bool(price and target_high and price > target_high)
    return {"target_offer_low": target_low, "target_offer_high": target_high,
            "discount_to_target_pct": required_discount, "negotiation_candidate": negotiation_candidate,
            "acquisition_score": min(100, score), "operator_score": operator,
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


def classify_deal(deal: dict) -> str:
    """Separate actionable acquisitions from early leads and irrelevant Reddit posts."""
    source = str(deal.get("source") or "")
    title = str(deal.get("title") or "").casefold()
    summary = str(deal.get("summary") or "").casefold()
    if source.startswith("reddit"):
        ignore = ("youtube channel", "first sale", "how do i", "how to sell",
                  "buying]", "[buying", "want to buy", "looking to buy",
                  "legal, technical", "business blind spots")
        if any(term in title for term in ignore):
            return "irrelevant"
        has_offer = any(term in title for term in
            ("selling my saas", "sell my saas", "saas for sale", "looking to sell my app",
             "selling my app", "for sale", "acquisition", "selling our saas"))
        has_partner = any(term in title for term in ("growth partner", "cofounder", "co-founder"))
        if has_partner and any(term in title + " " + summary for term in ("saas", "mrr", "arr")):
            return "operator"
        return "lead" if has_offer else "irrelevant"
    profit = number(deal.get("profit_monthly"))
    if profit is not None and profit >= 2000:
        return "qualified"
    if source in ("microns", "trustmrr", "buysellstartups", "acquire"):
        return "verify"
    return "irrelevant"


def build_actionable_digest(deals: list[dict], price_drops: list[dict],
                            raw_counts: dict | None = None) -> str:
    """Short decisions-first plain text digest; never promote unknown-profit leads."""
    groups = {"qualified": [], "verify": [], "operator": [], "lead": []}
    ignored = 0
    dedup = set()
    for d in deals:
        # Crossposts with the same headline are a single opportunity.
        key = (str(d.get("title") or "").casefold().strip()
               if str(d.get("source", "")).startswith("reddit")
               else str(d.get("id") or d.get("url")))
        if key in dedup:
            continue
        dedup.add(key)
        kind = classify_deal(d)
        if kind == "irrelevant":
            ignored += 1
        else:
            groups[kind].append(d)
    groups["qualified"].sort(key=lambda x: evaluate(x)["acquisition_score"], reverse=True)
    lines = ["SAAS SCOUT | ACTIONABLE DEALS", "Seller data is unverified until independently checked.", ""]
    if price_drops:
        lines.append(f"PRICE CUTS ({len(price_drops)})")
        for d in price_drops:
            v = evaluate(d)
            lines.append(f"  {d.get('title', 'Untitled')}")
            lines.append(f"  ${d['old_price']:,.0f} -> ${d['new_price']:,.0f} (-{d['discount_pct']}%)")
            if v["target_offer_high"] is not None:
                lines.append(f"  Negotiation ceiling (illustrative): ${v['target_offer_high']:,.0f}")
            lines.append(f"  {d.get('url', '')}")
        lines.append("")
    if groups["qualified"]:
        lines.append(f"UNDERWRITE / NEGOTIATE ({len(groups['qualified'])})")
        for d in groups["qualified"][:15]:
            v = evaluate(d)
            lines.append(f"  {d.get('title', 'Untitled')} | {d.get('source', '')}")
            lines.append(f"  Seller monthly profit: ${number(d.get('profit_monthly')):,.0f} | ask: ${number(d.get('price')):,.0f}" if number(d.get("price")) else f"  Seller monthly profit: ${number(d.get('profit_monthly')):,.0f} | ask unknown")
            if v["target_offer_high"] is not None:
                lines.append(f"  Illustrative target offer: ${v['target_offer_low']:,.0f}-${v['target_offer_high']:,.0f}")
                if v["discount_to_target_pct"] is not None:
                    lines.append(f"  Required discount to target: {v['discount_to_target_pct']:.1f}%")
            lines.append("  Verify: profit statements, subscription cohorts, organic traffic, workload, transferability")
            lines.append(f"  {d.get('url', '')}")
        lines.append("")
    if groups["verify"]:
        lines.append(f"RESEARCH FIRST / PROFIT UNKNOWN ({len(groups['verify'])})")
        for d in groups["verify"][:10]:
            revenue = number(d.get("annual_revenue"))
            lines.append(f"  {d.get('title', 'Untitled')} [{d.get('source', '')}]" +
                (f" | annual revenue ${revenue:,.0f}" if revenue else ""))
            lines.append(f"  {d.get('url', '')}")
        lines.append("  Do not estimate offers from revenue alone.")
        lines.append("")
    if groups["operator"] or groups["lead"]:
        lines.append(f"EARLY REDDIT LEADS ({len(groups['operator']) + len(groups['lead'])}) - ask for financials")
        for d in (groups["operator"] + groups["lead"])[:8]:
            lines.append(f"  {d.get('title', 'Untitled')} | {d.get('url', '')}")
        lines.append("")
    if not any(groups.values()) and not price_drops:
        lines.append("No qualified new acquisitions or price reductions this scan.")
        lines.append("")
    if raw_counts:
        lines.append("SOURCE HEALTH (raw parsed listings, not qualified deals)")
        lines.append(" | ".join(f"{source}: {count}" for source, count in raw_counts.items()))
        unhealthy = [source for source, count in raw_counts.items() if count == 0]
        if unhealthy:
            lines.append("CHECK COLLECTORS (0 results): " + ", ".join(unhealthy))
            lines.append("Zero can mean block, parsing regression, or no results; investigate.")
    lines.append(f"Discarded off-criteria/redundant new leads: {ignored}")
    return "\n".join(lines)
