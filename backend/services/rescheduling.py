"""Move a CONFIRMED appointment to another FREE slot. Same service, same fee, no payment."""
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Appointment, AppointmentStatus, DoctorService, Slot, SlotStatus
from app.timeutil import utcnow
from services import audit

MIN_NOTICE_HOURS = 2


def reschedule_appointment(db: Session, appointment_id: str, new_slot_id: str, now=None) -> dict:
    now = now or utcnow()
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise LookupError("not_found")
    if appt.status != AppointmentStatus.CONFIRMED:
        raise ValueError("not_reschedulable")
    old = db.get(Slot, appt.slot_id)
    new = db.get(Slot, new_slot_id)
    if new is None or new.start_at <= now:
        raise ValueError("bad_slot")
    if old.start_at - now < timedelta(hours=MIN_NOTICE_HOURS):
        raise ValueError("too_late")
    if appt.service_id:
        offers = db.scalar(select(DoctorService.service_id).where(
            DoctorService.doctor_id == new.doctor_id, DoctorService.service_id == appt.service_id))
        if offers is None:
            raise ValueError("bad_slot")
    claimed = db.execute(update(Slot).where(Slot.id == new_slot_id, Slot.status == SlotStatus.FREE)
                         .values(status=SlotStatus.BOOKED, held_by=None, held_until=None))
    if claimed.rowcount != 1:
        db.rollback()
        raise RuntimeError("slot_taken")
    old.status, old.held_by, old.held_until = SlotStatus.FREE, None, None
    appt.slot_id, appt.updated_at = new_slot_id, now
    info = {"booking_id": appt.id, "old_slot": old.id, "new_slot": new_slot_id}
    audit.log(db, actor="patient", action="booking_rescheduled", entity="appointment",
              entity_id=appt.id, session_id=appt.session_id, payload=info)
    db.commit()
    return info
