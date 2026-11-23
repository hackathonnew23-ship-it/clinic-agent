"""Time helpers.

Convention: the database stores NAIVE UTC datetimes. Convert to the clinic's
timezone (Asia/Kolkata by default) only at the edges (API input / output).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utcnow() -> datetime:
    """Current time as naive UTC (matches what is stored in the DB)."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(dt: datetime) -> str:
    """Naive-UTC datetime -> ISO-8601 string ending in Z."""
    return dt.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """ISO-8601 string -> timezone-aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local_to_utc(day: date, at: time, tz_name: str) -> datetime:
    """A wall-clock time in the clinic timezone -> naive UTC."""
    local = datetime.combine(day, at).replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(UTC).replace(tzinfo=None)


def utc_to_local(dt: datetime, tz_name: str) -> datetime:
    """Naive-UTC datetime -> aware datetime in the clinic timezone."""
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz_name))


def local_range_to_utc(date_from: date, date_to: date, tz_name: str) -> tuple[datetime, datetime]:
    """Inclusive local date range -> [start, end) as naive UTC."""
    start = local_to_utc(date_from, time(0, 0), tz_name)
    end = local_to_utc(date_to + timedelta(days=1), time(0, 0), tz_name)
    return start, end


def part_of_day(local_hour: int) -> str:
    """morning < 12:00, afternoon 12:00-16:59, evening >= 17:00 (clinic local time)."""
    if local_hour < 12:
        return "morning"
    if local_hour < 17:
        return "afternoon"
    return "evening"
