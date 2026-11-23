"""Payment state machine: HELD -> PAYMENT_PENDING -> CONFIRMED | FAILED | EXPIRED.

Booking is confirmed ONLY after settle_payment(success=True) and then the provider's
confirm(). Gateway is a mock now; Razorpay test mode plugs into start_payment /
settle_payment in Phase 4. The LLM cannot settle a payment: only the app can.
"""
from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, AppointmentStatus, Base, Payment, Slot, SlotStatus, new_id
from app.timeutil import utcnow
from services import audit
from services.errors import BookingError
from services.flow_errors import FlowError

PAYMENT_WINDOW_SECONDS = 300
PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")


def fee_paise(db: Session, service_id: str) -> int:
    tbl = Base.metadata.tables["services"]
    row = db.execute(select(tbl).where(tbl.c.id == service_id)).mappings().first()
    if row is None:
        raise FlowError("unknown_service", "Unknown service.")
    keys = [k for k in row.keys() if "paise" in k] + [k for k in row.keys() if k in ("fee", "price", "amount")]
    for k in keys:
        if isinstance(row[k], int) and row[k] > 0:
            return row[k]
    raise FlowError("no_fee", "This service has no fee configured.")


def _free_slot(slot: Slot | None) -> None:
    if slot is not None and slot.status == SlotStatus.HELD:
        slot.status = SlotStatus.FREE
        slot.held_by = None
        slot.held_until = None


def start_payment(db: Session, appointment_id: str, session_id: str) -> Payment:
    now = utcnow()
    appt = db.get(Appointment, appointment_id)
    if appt is None or appt.status not in AppointmentStatus.IN_FLIGHT:
        raise FlowError("hold_expired", "Your slot hold has expired. Please pick a slot again.")
    slot = db.get(Slot, appt.slot_id)
    if slot is not None and slot.held_until is not None and slot.held_until < now:
        appt.status, appt.updated_at = AppointmentStatus.EXPIRED, now
        _free_slot(slot)
        audit.log(db, actor="system", action="hold_expired", entity="appointment", entity_id=appt.id, session_id=session_id)
        db.commit()
        raise FlowError("hold_expired", "Your slot hold has expired. Please pick a slot again.")
    existing = db.scalar(select(Payment).where(Payment.appointment_id == appt.id, Payment.status == "CREATED"))
    if existing:
        return existing
    pay = Payment(
        id=new_id("pay"), appointment_id=appt.id, provider=PROVIDER, provider_order_id=new_id("order"),
        amount_paise=fee_paise(db, appt.service_id), status="CREATED", idempotency_key=f"pay:{appt.id}",
    )
    db.add(pay)
    appt.status, appt.updated_at = AppointmentStatus.PAYMENT_PENDING, now
    if slot is not None:  # give the patient time to pay
        slot.held_until = max(slot.held_until or now, now + timedelta(seconds=PAYMENT_WINDOW_SECONDS))
    audit.log(db, actor="system", action="payment_started", entity="payment", entity_id=pay.id, session_id=session_id,
              payload={"appointment": appt.id, "amount_paise": pay.amount_paise, "provider": PROVIDER})
    db.commit()
    return pay


def settle_payment(db: Session, payment_id: str, success: bool) -> Payment:
    """Gateway result (mock button now, Razorpay webhook later). Idempotent."""
    pay = db.get(Payment, payment_id)
    if pay is None:
        raise FlowError("no_payment", "Payment not found.")
    if pay.status in ("SUCCEEDED", "FAILED", "REFUND_DUE"):
        return pay
    now = utcnow()
    appt = db.get(Appointment, pay.appointment_id)
    slot = db.get(Slot, appt.slot_id) if appt else None
    if success:
        if appt is None or appt.status not in AppointmentStatus.IN_FLIGHT:
            pay.status = "REFUND_DUE"  # paid after the hold expired: refund, do not confirm
            audit.log(db, actor="system", action="payment_after_expiry", entity="payment", entity_id=pay.id)
            db.commit()
            raise FlowError("hold_expired", "The slot hold expired before payment completed. Your money will be refunded.")
        pay.status = "SUCCEEDED"
        audit.log(db, actor="system", action="payment_succeeded", entity="payment", entity_id=pay.id,
                  payload={"amount_paise": pay.amount_paise})
    else:
        pay.status = "FAILED"
        if appt is not None:
            appt.status, appt.updated_at = AppointmentStatus.FAILED, now
        _free_slot(slot)
        audit.log(db, actor="system", action="payment_failed", entity="payment", entity_id=pay.id)
    db.commit()
    return pay


def confirm_after_payment(db: Session, provider, pay: Payment, patient_id: str):
    """Ask the BookingProvider to confirm. Only valid when pay.status == SUCCEEDED."""
    if pay.status != "SUCCEEDED":
        raise FlowError("not_paid", "Payment has not succeeded.")
    appt = db.get(Appointment, pay.appointment_id)
    try:
        return provider.confirm(pay.appointment_id, patient_id, pay.id)
    except BookingError:
        # The clinic layer may only confirm from HELD; step back once and retry.
        if appt is not None and appt.status == AppointmentStatus.PAYMENT_PENDING:
            appt.status = AppointmentStatus.HELD
            db.commit()
            return provider.confirm(pay.appointment_id, patient_id, pay.id)
        raise
