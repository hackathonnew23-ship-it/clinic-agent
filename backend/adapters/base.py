"""The BookingProvider contract.

Every clinic backend (mock REST system, Google Calendar, a real HIS later) implements
this protocol. The agent's tools call ONLY these methods, never the database.
Switching backend = changing BOOKING_PROVIDER in .env.

Note: hold_slot takes an optional service_id (an addition to the original six-method
sketch) so the hold knows which service it is for, which payment needs for the fee.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

# Re-export the typed errors so adapter authors import from one place.
from services.errors import (  # noqa: F401
    BookingError,
    BookingNotFound,
    HoldExpired,
    PatientNotFound,
    ProviderError,
    SlotUnavailable,
)


@dataclass(frozen=True)
class SlotDTO:
    id: str
    doctor_id: str
    start_at: datetime  # timezone-aware (UTC)
    end_at: datetime


@dataclass(frozen=True)
class HoldDTO:
    hold_id: str  # the appointment id
    slot_id: str
    session_id: str
    expires_at: datetime  # timezone-aware (UTC)
    service_id: str | None = None


@dataclass(frozen=True)
class PatientDTO:
    id: str
    mobile: str
    name: str | None
    is_new: bool


@dataclass(frozen=True)
class BookingDTO:
    id: str
    slot_id: str
    patient_id: str | None
    status: str
    start_at: datetime
    ics_uid: str | None = None
    external_ref: str | None = None


@dataclass(frozen=True)
class CancelResultDTO:
    booking_id: str
    status: str


@runtime_checkable
class BookingProvider(Protocol):
    name: str

    def get_slots(
        self,
        doctor_id: str,
        service_id: str,
        date_from: date,
        date_to: date,
        part_of_day: str | None = None,
    ) -> list[SlotDTO]: ...

    def hold_slot(
        self,
        slot_id: str,
        session_id: str,
        ttl_seconds: int = 300,
        service_id: str | None = None,
    ) -> HoldDTO: ...

    def find_or_create_patient(self, mobile: str, name: str | None = None) -> PatientDTO: ...

    def confirm(self, hold_id: str, patient_id: str, payment_ref: str) -> BookingDTO: ...

    def reschedule(self, booking_id: str, new_slot_id: str) -> BookingDTO: ...

    def cancel(self, booking_id: str, reason: str) -> CancelResultDTO: ...
