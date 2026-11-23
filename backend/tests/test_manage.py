from datetime import timedelta

import pytest
from sqlalchemy import select

from agent.tools import BookingTools
from app.models import Appointment, AppointmentStatus, Slot, SlotStatus, new_id
from app.timeutil import utcnow
from services.rescheduling import reschedule_appointment


def _booked(db, slot_id, hours=None):
    slot = db.get(Slot, slot_id)
    slot.status = SlotStatus.BOOKED
    if hours is not None:
        slot.start_at = utcnow() + timedelta(hours=hours)
    appt = Appointment(id=new_id("appt"), slot_id=slot_id, service_id="svc_cleaning",
                       status=AppointmentStatus.CONFIRMED, session_id="t")
    db.add(appt)
    db.commit()
    return appt


def _other(db, slot_id):
    return db.scalar(select(Slot.id).where(
        Slot.doctor_id == "doc_rao", Slot.status == SlotStatus.FREE, Slot.id != slot_id,
        Slot.start_at > utcnow() + timedelta(days=2)).order_by(Slot.start_at.desc()).limit(1))


def test_reschedule_moves_booking(db, slot_id):
    appt = _booked(db, slot_id)
    target = _other(db, slot_id)
    reschedule_appointment(db, appt.id, target)
    db.refresh(appt)
    assert appt.slot_id == target and appt.status == AppointmentStatus.CONFIRMED
    assert db.get(Slot, slot_id).status == SlotStatus.FREE
    assert db.get(Slot, target).status == SlotStatus.BOOKED


def test_reschedule_too_late(db, slot_id):
    appt = _booked(db, slot_id, hours=1)
    with pytest.raises(ValueError):
        reschedule_appointment(db, appt.id, _other(db, slot_id))


def test_reschedule_to_taken_slot_fails(db, slot_id):
    appt = _booked(db, slot_id)
    target = _other(db, slot_id)
    db.get(Slot, target).status = SlotStatus.BOOKED
    db.commit()
    with pytest.raises(RuntimeError):
        reschedule_appointment(db, appt.id, target)
    assert db.get(Slot, slot_id).status == SlotStatus.BOOKED


def test_cancelled_booking_cannot_move(db, slot_id):
    appt = _booked(db, slot_id)
    appt.status = AppointmentStatus.CANCELLED
    db.commit()
    with pytest.raises(ValueError):
        reschedule_appointment(db, appt.id, _other(db, slot_id))


def test_ai_cancel_needs_otp():
    assert BookingTools(provider=object()).run("cancel_booking", {})["error"] == "not_verified"


def test_ai_reschedule_needs_otp():
    assert BookingTools(provider=object()).run("reschedule_booking", {"new_slot_id": "x"})["error"] == "not_verified"


def test_ai_cancel_needs_quote_first():
    t = BookingTools(provider=object())
    t._manage = {"appt_id": "a", "verified": True}
    assert t.run("cancel_booking", {})["error"] == "quote_first"


def test_ai_cannot_use_unoffered_slot():
    t = BookingTools(provider=object())
    t._manage = {"appt_id": "a", "verified": True}
    assert t.run("reschedule_booking", {"new_slot_id": "slot_fake"})["error"] == "not_offered"
