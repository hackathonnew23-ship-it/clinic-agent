from datetime import timedelta

import pytest

from app.models import Appointment, AppointmentStatus, Payment, Slot, SlotStatus, new_id
from app.timeutil import utcnow
from services.cancellation import cancel_appointment
from services.flow_errors import FlowError
from services.otp import send_otp, verify_otp
from services.payments import settle_payment

MOBILE = "9876543210"


# ------------------------------------------------------------------ OTP
def test_otp_correct_code_verifies(db):
    out = send_otp(db, "s1", MOBILE)
    assert verify_otp(db, "s1", out["test_code"]).endswith(MOBILE)


def test_otp_locks_after_3_wrong(db):
    out = send_otp(db, "s2", MOBILE)
    bad = "111111" if out["test_code"] != "111111" else "222222"
    for _ in range(3):
        with pytest.raises(FlowError):
            verify_otp(db, "s2", bad)
    with pytest.raises(FlowError):  # even the right code is refused once locked
        verify_otp(db, "s2", out["test_code"])


def test_otp_resend_cooldown_30s(db):
    t0 = utcnow()
    send_otp(db, "s3", MOBILE, now=t0)
    with pytest.raises(FlowError):
        send_otp(db, "s3", MOBILE, now=t0 + timedelta(seconds=10))
    send_otp(db, "s3", MOBILE, now=t0 + timedelta(seconds=31))


def test_otp_expires_after_5_min(db):
    t0 = utcnow()
    out = send_otp(db, "s4", MOBILE, now=t0)
    with pytest.raises(FlowError):
        verify_otp(db, "s4", out["test_code"], now=t0 + timedelta(seconds=301))


# ------------------------------------------------------------------ helpers
def _appt(db, slot_id, status, pay_status, hours=None, amount=60000):
    slot = db.get(Slot, slot_id)
    if hours is not None:
        slot.start_at = utcnow() + timedelta(hours=hours)
    else:
        slot.status = SlotStatus.HELD
    appt = Appointment(id=new_id("appt"), slot_id=slot_id, status=status, session_id="t")
    db.add(appt)
    db.flush()
    pay = Payment(id=new_id("pay"), appointment_id=appt.id, provider="mock", amount_paise=amount,
                  status=pay_status, idempotency_key=f"k:{appt.id}")
    db.add(pay)
    db.commit()
    return appt, pay, slot


# ------------------------------------------------------------------ payment
def test_payment_success(db, slot_id):
    appt, pay, _ = _appt(db, slot_id, AppointmentStatus.PAYMENT_PENDING, "CREATED")
    assert settle_payment(db, pay.id, True).status == "SUCCEEDED"


def test_payment_failure_frees_slot(db, slot_id):
    appt, pay, slot = _appt(db, slot_id, AppointmentStatus.PAYMENT_PENDING, "CREATED")
    assert settle_payment(db, pay.id, False).status == "FAILED"
    db.refresh(appt); db.refresh(slot)
    assert appt.status == AppointmentStatus.FAILED
    assert slot.status == SlotStatus.FREE


def test_late_payment_marked_refund_due(db, slot_id):
    appt, pay, _ = _appt(db, slot_id, AppointmentStatus.EXPIRED, "CREATED")
    with pytest.raises(FlowError):
        settle_payment(db, pay.id, True)
    db.refresh(pay)
    assert pay.status == "REFUND_DUE"


# ------------------------------------------------------------------ cancel bands
@pytest.mark.parametrize("hours,pct,refund", [(48, 100, 60000), (5, 50, 30000), (1, 0, 0)])
def test_cancel_refund_bands(db, slot_id, hours, pct, refund):
    appt, pay, slot = _appt(db, slot_id, AppointmentStatus.CONFIRMED, "SUCCEEDED", hours=hours)
    r = cancel_appointment(db, appt.id, "test")
    assert (r["refund_percent"], r["refund_paise"]) == (pct, refund)
    db.refresh(appt)
    assert appt.status == AppointmentStatus.CANCELLED


def test_cannot_cancel_twice(db, slot_id):
    appt, _, _ = _appt(db, slot_id, AppointmentStatus.CONFIRMED, "SUCCEEDED", hours=48)
    cancel_appointment(db, appt.id, "x")
    with pytest.raises(ValueError):
        cancel_appointment(db, appt.id, "x")
