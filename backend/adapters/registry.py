"""Picks the booking backend from config. Switching adapter is a config change only."""
from __future__ import annotations

from adapters.base import BookingProvider
from app.config import get_settings


def get_provider(name: str | None = None) -> BookingProvider:
    chosen = (name or get_settings().booking_provider).lower()

    if chosen == "mock_rest":
        from adapters.mock_rest import MockRestAdapter

        return MockRestAdapter()

    if chosen == "google_calendar":
        from adapters.google_calendar import GoogleCalendarAdapter

        return GoogleCalendarAdapter()

    raise ValueError(f"Unknown BOOKING_PROVIDER '{chosen}'. Use mock_rest or google_calendar.")
