"""Refund bands. Rules come from clinic.refund_rules_json, with the brief's defaults."""
import json
from datetime import datetime, timezone

DEFAULT_RULES = [
    {"min_hours": 24, "refund_percent": 100},
    {"min_hours": 2, "refund_percent": 50},
    {"min_hours": 0, "refund_percent": 0},
]


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def load_rules(rules_json: str | None) -> list[dict]:
    try:
        rules = json.loads(rules_json or "[]")
    except ValueError:
        rules = []
    return sorted(rules or DEFAULT_RULES, key=lambda r: -r["min_hours"])


def refund_percent(slot_start: datetime, now: datetime, rules_json: str | None = None) -> int:
    hours = (_naive_utc(slot_start) - _naive_utc(now)).total_seconds() / 3600
    for r in load_rules(rules_json):
        if hours >= r["min_hours"]:
            return int(r["refund_percent"])
    return 0


def refund_amount(paid_paise: int, slot_start: datetime, now: datetime, rules_json: str | None = None) -> tuple[int, int]:
    pct = refund_percent(slot_start, now, rules_json)
    return pct, paid_paise * pct // 100
