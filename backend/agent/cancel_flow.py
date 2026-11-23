"""Scripted cancel flow: ref -> OTP to mobile on file -> show refund -> confirm. English only."""
import re
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Appointment, AppointmentStatus, Clinic, Patient, Payment, Slot
from app.timeutil import utcnow
from services.cancellation import cancel_appointment
from services.flow_errors import FlowError
from services.otp import send_otp, verify_otp
from services.refunds import refund_amount

TRIGGERS = ("cancel my", "cancel appointment", "cancel booking", "cancel the appointment")


def is_cancel_intent(t_: str) -> bool:
    return any(w in t_ for w in TRIGGERS)


def _find(db, ref: str):
    ref = ref.strip().upper().replace("APPT_", "")
    if len(ref) < 6:
        return None
    for a in db.scalars(select(Appointment).where(Appointment.status == AppointmentStatus.CONFIRMED)):
        if ref in a.id.upper() or (a.external_ref and ref in a.external_ref.upper()):
            return a
    return None


def _msg(e: Exception) -> str:
    return getattr(e, "message", None) or str(e)


def _refund_text(db, appt) -> str:
    slot = db.get(Slot, appt.slot_id)
    pay = db.scalars(select(Payment).where(Payment.appointment_id == appt.id,
                     Payment.status.not_in(["CREATED", "FAILED"]))).first()
    clinic = db.get(Clinic, "clinic_smile")
    pct, refund = refund_amount(pay.amount_paise if pay else 0, slot.start_at, utcnow(),
                                clinic.refund_rules_json if clinic else None)
    return f"Verified. You would get a refund of Rs {refund / 100:.0f} ({pct}%). Cancel this appointment? Reply yes or no."


def cancel_turn(t_: str, state: dict, tools=None) -> str:
    stage = state.get("stage")
    sid = state.setdefault("cancel_sid", getattr(tools, "session_id", None) or "cx_" + uuid.uuid4().hex[:20])

    if stage == "cancel_confirm":
        if t_ in ("yes", "y", "confirm", "ok"):
            appt_id = state.pop("cancel_id")
            state.pop("stage"); state.pop("cancel_sid", None)
            with SessionLocal() as db:
                r = cancel_appointment(db, appt_id, "patient request via chat")
            return (f"Your appointment is cancelled. Refund: Rs {r['refund_paise'] / 100:.0f} "
                    f"({r['refund_percent']}% of Rs {r['paid_paise'] / 100:.0f}).")
        state.pop("stage"); state.pop("cancel_id", None); state.pop("cancel_sid", None)
        return "Okay, I kept your appointment."

    if stage == "cancel_otp":
        with SessionLocal() as db:
            appt = db.get(Appointment, state["cancel_id"])
            patient = db.get(Patient, appt.patient_id) if appt and appt.patient_id else None
            try:
                if "resend" in t_:
                    out = send_otp(db, sid, patient.mobile)
                    return f"New code sent to {out['masked']}."
                m = re.search(r"(?<!\d)(\d{6})(?!\d)", t_)
                if not m:
                    return "Please type the 6-digit code I sent, or type 'resend'."
                verify_otp(db, sid, m.group(1))
            except FlowError as e:
                return _msg(e)
            state["stage"] = "cancel_confirm"
            return _refund_text(db, appt)

    # stage is cancel_ref or a new cancel request
    if stage != "cancel_ref":
        state["stage"] = "cancel_ref"
        return "Sure. Please send your booking reference (it is in your confirmation message)."
    with SessionLocal() as db:
        appt = _find(db, t_)
        if appt is None:
            return "I could not find a confirmed booking with that reference. Please check it and send it again, or type 'restart'."
        patient = db.get(Patient, appt.patient_id) if appt.patient_id else None
        if patient is None or not getattr(patient, "mobile", None):
            return "I cannot verify this booking automatically. Please contact the clinic staff."
        try:
            out = send_otp(db, sid, patient.mobile)
        except FlowError as e:
            return _msg(e)
        state.update(stage="cancel_otp", cancel_id=appt.id)
    return f"For your security I sent a code to {out['masked']}. Please type it here."
