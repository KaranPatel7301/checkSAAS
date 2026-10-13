#!/usr/bin/env python3
"""Persistent acquisition decision pipeline. Scanner observations never overwrite human decisions."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path

STATUSES = ("new", "investigating", "negotiating", "waiting_for_financials", "passed", "acquired")
EVIDENCE = ("unverified", "broker_reported", "seller_documents", "independently_verified")
DEFAULT_PATH = "deal_decisions.json"
CHECKLIST = ("stripe_exports", "profit_and_expenses", "m1_m6_m12_retention", "organic_traffic", "customer_acquisition", "owner_hours", "code_and_ip", "transferability")


def load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Pipeline must be a JSON object keyed by stable listing ID")
    return data


def save(path: str, records: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, target)


def sync(history: dict, decisions: dict, today: str | None = None) -> tuple[dict, int]:
    """Append missing listings and refresh scanner fields, never overwrite decisions/reviews."""
    today = today or dt.date.today().isoformat()
    count = 0
    for key, listing in history.items():
        if not isinstance(listing, dict):
            continue
        if key not in decisions:
            decisions[key] = {
                "status": "new",
                "evidence_grade": "unverified",
                "review_notes": [],
                "diligence": {item: False for item in CHECKLIST},
                "created_at": today,
                "updated_at": today,
            }
            count += 1
        item = decisions[key]
        item["listing"] = {
            "title": listing.get("title"),
            "source": listing.get("source"),
            "url": listing.get("url"),
            "last_seen": listing.get("last_seen"),
            "category": listing.get("category"),
            "screening": listing.get("screening"),
            "financials": listing.get("financials"),
            "latest_price": (listing.get("prices") or [{}])[-1].get("amount"),
        }
    return decisions, count


def set_status(decisions: dict, deal_id: str, status: str, note: str, date: str | None = None) -> None:
    if deal_id not in decisions:
        raise ValueError("Unknown deal ID: " + deal_id)
    if status not in STATUSES:
        raise ValueError("Invalid status: " + status)
    if not note.strip():
        raise ValueError("A reason is required to change a status")
    date = date or dt.datetime.now(dt.timezone.utc).isoformat()
    item = decisions[deal_id]
    old = item["status"]
    item["status"] = status
    item["updated_at"] = date
    item.setdefault("review_notes", []).append({"at": date, "type": "status_change", "from": old, "to": status, "note": note.strip()})


def add_review(decisions: dict, deal_id: str, verdict: str, note: str, evidence: str, date: str | None = None) -> None:
    if deal_id not in decisions:
        raise ValueError("Unknown deal ID: " + deal_id)
    if evidence not in EVIDENCE:
        raise ValueError("Invalid evidence grade")
    if not note.strip():
        raise ValueError("Review note cannot be empty")
    date = date or dt.datetime.now(dt.timezone.utc).isoformat()
    item = decisions[deal_id]
    item["evidence_grade"] = evidence
    item["updated_at"] = date
    item.setdefault("review_notes", []).append({"at": date, "type": "review", "verdict": verdict, "note": note.strip(), "evidence": evidence})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default="deal_history.json")
    parser.add_argument("--pipeline", default=os.environ.get("SAAS_SCOUT_PIPELINE", DEFAULT_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    status = sub.add_parser("set-status")
    status.add_argument("deal_id")
    status.add_argument("status", choices=STATUSES)
    status.add_argument("--note", required=True)
    review = sub.add_parser("review")
    review.add_argument("deal_id")
    review.add_argument("verdict")
    review.add_argument("--note", required=True)
    review.add_argument("--evidence", choices=EVIDENCE, default="broker_reported")
    sub.add_parser("summary")
    args = parser.parse_args()
    decisions = load(args.pipeline)
    if args.command == "sync":
        decisions, created = sync(load(args.history), decisions)
        save(args.pipeline, decisions)
        print(f"Pipeline synced: {len(decisions)} deals, {created} new")
    elif args.command == "set-status":
        set_status(decisions, args.deal_id, args.status, args.note)
        save(args.pipeline, decisions)
    elif args.command == "review":
        add_review(decisions, args.deal_id, args.verdict, args.note, args.evidence)
        save(args.pipeline, decisions)
    elif args.command == "summary":
        counts = {s: sum(1 for d in decisions.values() if d.get("status") == s) for s in STATUSES}
        print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
