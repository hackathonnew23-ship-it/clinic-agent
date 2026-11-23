"""Typed booking errors shared by services, adapters and the API layer."""
from __future__ import annotations


class BookingError(Exception):
    code = "booking_error"


class SlotUnavailable(BookingError):
    """The slot is held by someone else, already booked, in the past, or does not exist."""

    code = "slot_unavailable"

    def __init__(self, slot_id: str, reason: str = "unavailable") -> None:
        super().__init__(f"slot {slot_id} is not available ({reason})")
        self.slot_id = slot_id
        self.reason = reason


class HoldExpired(BookingError):
    """The hold ran out (or was taken over) before the booking could be confirmed."""

    code = "hold_expired"


class BookingNotFound(BookingError):
    code = "booking_not_found"


class PatientNotFound(BookingError):
    code = "patient_not_found"


class ProviderError(BookingError):
    """The external clinic system failed or returned something unexpected."""

    code = "provider_error"
