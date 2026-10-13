"""Persist collector health separately from deal relevance.

Zero parsed listings is NOT evidence that a marketplace has no deals.
The status record differentiates observed zeros from healthy fetches.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path


SOURCE_NAMES = ("Flippa", "Empire Flippers", "TrustMRR",
                "Buy Sell Startups", "Microns", "Reddit", "Acquire")


def load_health(path=None):
    file = Path(path or os.environ.get("SAAS_SCOUT_SOURCE_HEALTH", "source_health.json"))
    if not file.exists():
        return {}
    data = json.loads(file.read_text())
    if not isinstance(data, dict):
        raise ValueError("Source health must be a JSON object")
    return data


def update_health(raw_counts, prior, timestamp=None):
    timestamp = timestamp or dt.datetime.now(dt.timezone.utc).isoformat()
    alerts = []
    for source in SOURCE_NAMES:
        count = int(raw_counts.get(source, 0))
        before = prior.get(source, {})
        streak = int(before.get("consecutive_zeros", 0)) + 1 if count == 0 else 0
        status = "suspected_failure" if streak >= 2 else ("unverified_zero" if count == 0 else "parsed")
        prior[source] = {
            "last_checked": timestamp,
            "last_parsed_count": count,
            "consecutive_zeros": streak,
            "status": status,
            "last_nonzero": timestamp if count else before.get("last_nonzero"),
        }
        if streak == 2 or (streak > 2 and streak % 6 == 0):
            alerts.append(f"{source}: {streak} consecutive zero-result scans; inspect HTTP errors, authentication, pagination and selectors")
        if count and int(before.get("consecutive_zeros", 0)) >= 2:
            alerts.append(f"{source}: collector recovered, parsed {count} listings")
    return alerts


def save_health(state, path=None):
    file = Path(path or os.environ.get("SAAS_SCOUT_SOURCE_HEALTH", "source_health.json"))
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(file)
