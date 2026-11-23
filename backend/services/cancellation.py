"""Cancel a CONFIRMED appointment: free the slot, compute and record the refund, audit it."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, AppointmentStatus, Clinic, Payment, Slot, SlotStatus
from app.timeutil import utcnow
from services import audit
from services.refunds import refund_amount


def cancel_appointment(db: Session, appointment_id: str, reason: str = "", now=None) -> dict:
    now = now or utcnow()
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise LookupError("not_found")
    if appt.status != AppointmentStatus.CONFIRMED:
        raise ValueError("not_cancellable")
    slot = db.get(Slot, appt.slot_id)
    clinic = db.get(Clinic, "clinic_smile")
    pay = db.scalars(
        select(Payment).where(Payment.appointment_id == appt.id, Payment.status.not_in(["CREATED", "FAILED"]))
        .order_by(Payment.created_at.desc())
    ).first()
    paid = pay.amount_paise if pay else 0
    pct, refund = refund_amount(paid, slot.start_at, now, clinic.refund_rules_json if clinic else None)
    appt.status = AppointmentStatus.CANCELLED
    appt.updated_at = now
    if slot.start_at > now:
        slot.status = SlotStatus.FREE
        slot.held_by = None
        slot.held_until = None
    if pay and refund > 0:
        pay.status = "REFUNDED" if pct == 100 else "PARTIAL_REFUND"
        pay.raw_json = json.dumps({"refund_paise": refund, "refund_percent": pct})
    info = {"booking_id": appt.id, "status": "CANCELLED", "paid_paise": paid,
            "refund_percent": pct, "refund_paise": refund}
    audit.log(db, actor="patient", action="booking_cancelled", entity="appointment",
              entity_id=appt.id, session_id=appt.session_id, payload={**info, "reason": reason})
    db.commit()
    return info
