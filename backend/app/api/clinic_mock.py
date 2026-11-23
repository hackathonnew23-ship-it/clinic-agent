"""A small FHIR-style mock clinic system (Slot / Appointment / Patient).

Not real FHIR compliance: just enough resource shapes (Bundle, Slot, Appointment,
Patient, OperationOutcome) to be a believable "clinic's own system" the adapter talks to.
It is a thin HTTP layer over services/holds.py and services/patients.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Appointment, AppointmentStatus, Slot
from app.timeutil import iso_utc
from services import holds, patients
from services.errors import BookingNotFound, HoldExpired, SlotUnavailable

router = APIRouter(prefix="/mock-clinic/fhir", tags=["mock-clinic (FHIR-style)"])

_FHIR_STATUS = {
    AppointmentStatus.HELD: "proposed",
    AppointmentStatus.PAYMENT_PENDING: "pending",
    AppointmentStatus.CONFIRMED: "booked",
    AppointmentStatus.CANCELLED: "cancelled",
    AppointmentStatus.RESCHEDULED: "cancelled",
    AppointmentStatus.FAILED: "noshow",
    AppointmentStatus.EXPIRED: "noshow",
}


# --------------------------------------------------------------------------- request bodies
class HoldRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    ttl_seconds: int = Field(default=300, ge=30, le=900)
    service_id: str | None = None


class PatientRequest(BaseModel):
    mobile: str = Field(min_length=10, max_length=20)
    name: str | None = Field(default=None, max_length=80)


class ConfirmRequest(BaseModel):
    patient_id: str
    payment_ref: str = Field(min_length=1, max_length=128)


# --------------------------------------------------------------------------- resource builders
def outcome(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": code, "diagnostics": message}],
        },
    )


def slot_resource(slot: Slot) -> dict[str, Any]:
    return {
        "resourceType": "Slot",
        "id": slot.id,
        "schedule": {"reference": f"Schedule/{slot.doctor_id}"},
        "status": "free",
        "start": iso_utc(slot.start_at),
        "end": iso_utc(slot.end_at),
    }


def appointment_resource(appt: Appointment, slot: Slot) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Appointment",
        "id": appt.id,
        "status": _FHIR_STATUS.get(appt.status, "proposed"),
        "slot": [{"reference": f"Slot/{appt.slot_id}"}],
        "start": iso_utc(slot.start_at),
        "end": iso_utc(slot.end_at),
        "participant": [{"actor": {"reference": f"Practitioner/{slot.doctor_id}"}, "status": "accepted"}],
        "extension": [],
        "identifier": [],
    }
    if appt.patient_id:
        resource["participant"].append(
            {"actor": {"reference": f"Patient/{appt.patient_id}"}, "status": "accepted"}
        )
    if appt.status in AppointmentStatus.IN_FLIGHT and slot.held_until:
        resource["extension"].append({"url": "hold-expires", "valueDateTime": iso_utc(slot.held_until)})
    if appt.ics_uid:
        resource["extension"].append({"url": "ics-uid", "valueString": appt.ics_uid})
    if appt.external_ref:
        resource["identifier"].append({"system": "clinic-ref", "value": appt.external_ref})
    return resource


# --------------------------------------------------------------------------- endpoints
@router.get("/Slot")
def search_slots(
    schedule: str,
    start: date,
    end: date,
    service_type: str | None = None,  # accepted for FHIR shape; Phase 1 uses a fixed 30-min grid
    part_of_day: Literal["morning", "afternoon", "evening"] | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    slots = holds.get_available_slots(db, schedule, start, end, part_of_day)
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(slots),
        "entry": [{"resource": slot_resource(s)} for s in slots],
    }


@router.post("/Slot/{slot_id}/$hold", status_code=201)
def hold_slot(slot_id: str, body: HoldRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    appt = holds.hold_slot(
        db, slot_id, body.session_id, ttl_seconds=body.ttl_seconds, service_id=body.service_id
    )
    slot = db.get(Slot, slot_id)
    assert slot is not None
    return appointment_resource(appt, slot)


@router.post("/Patient/$find-or-create")
def find_or_create_patient(body: PatientRequest, db: Session = Depends(get_db)) -> Any:
    try:
        patient, created = patients.find_or_create_patient(db, body.mobile, body.name)
    except ValueError as exc:
        return outcome(422, "invalid", str(exc))
    return {
        "resourceType": "Patient",
        "id": patient.id,
        "name": [{"text": patient.name}] if patient.name else [],
        "telecom": [{"system": "phone", "value": patient.mobile}],
        "extension": [{"url": "is-new", "valueBoolean": created}],
    }


@router.post("/Appointment/{appointment_id}/$confirm")
def confirm_appointment(
    appointment_id: str, body: ConfirmRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    appt = holds.confirm_booking(db, appointment_id, body.patient_id, body.payment_ref)
    slot = db.get(Slot, appt.slot_id)
    assert slot is not None
    return appointment_resource(appt, slot)


@router.get("/Appointment/{appointment_id}")
def read_appointment(appointment_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    appt = holds.get_appointment(db, appointment_id)
    slot = db.get(Slot, appt.slot_id)
    assert slot is not None
    return appointment_resource(appt, slot)


# --------------------------------------------------------------------------- error mapping
def register_error_handlers(app: Any) -> None:
    async def _slot_unavailable(_request: Any, exc: SlotUnavailable) -> JSONResponse:
        return outcome(409, "conflict", str(exc))

    async def _hold_expired(_request: Any, exc: HoldExpired) -> JSONResponse:
        return outcome(410, "expired", str(exc))

    async def _not_found(_request: Any, exc: BookingNotFound) -> JSONResponse:
        return outcome(404, "not-found", str(exc))

    app.add_exception_handler(SlotUnavailable, _slot_unavailable)
    app.add_exception_handler(HoldExpired, _hold_expired)
    app.add_exception_handler(BookingNotFound, _not_found)


class CancelRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


@router.post("/Appointment/{appointment_id}/$cancel")
def cancel_appointment_endpoint(appointment_id: str, body: CancelRequest, db: Session = Depends(get_db)) -> Any:
    from services.cancellation import cancel_appointment

    try:
        return cancel_appointment(db, appointment_id, body.reason)
    except LookupError:
        return outcome(404, "not-found", "Appointment not found.")
    except ValueError:
        return outcome(409, "not-cancellable", "Only confirmed appointments can be cancelled.")


class RescheduleRequest(BaseModel):
    new_slot_id: str


@router.post("/Appointment/{appointment_id}/$reschedule")
def reschedule_appointment_endpoint(appointment_id: str, body: RescheduleRequest, db: Session = Depends(get_db)) -> Any:
    from services.rescheduling import reschedule_appointment

    try:
        reschedule_appointment(db, appointment_id, body.new_slot_id)
    except LookupError:
        return outcome(404, "not-found", "Appointment not found.")
    except RuntimeError:
        return outcome(409, "slot-taken", "That slot was just taken.")
    except ValueError as exc:
        return outcome(422, str(exc), "Cannot reschedule: " + str(exc).replace("_", " "))
    appt = holds.get_appointment(db, appointment_id)
    slot = db.get(Slot, appt.slot_id)
    return appointment_resource(appt, slot)
