"""Hold expiry, takeover, confirmation and the DB-level backstop."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Appointment, AppointmentStatus, AuditLog, Slot, SlotStatus, new_id
from app.timeutil import utcnow
from services import holds
from services.errors import HoldExpired, SlotUnavailable
from services.patients import find_or_create_patient, normalize_mobile


def test_second_hold_is_rejected_while_first_is_active(db, slot_id):
    first = holds.hold_slot(db, slot_id, "s1", service_id="svc_cleaning")
    assert first.status == AppointmentStatus.HELD
    with pytest.raises(SlotUnavailable):
        holds.hold_slot(db, slot_id, "s2")


def test_unknown_slot_is_rejected(db):
    with pytest.raises(SlotUnavailable) as err:
        holds.hold_slot(db, "slot_does_not_exist", "s1")
    assert err.value.reason == "not_found"


def test_expired_hold_can_be_taken_over(db, slot_id):
    t0 = utcnow()
    first = holds.hold_slot(db, slot_id, "s1", ttl_seconds=300, now=t0)

    with pytest.raises(SlotUnavailable):  # 1 second before expiry: still blocked
        holds.hold_slot(db, slot_id, "s2", now=t0 + timedelta(seconds=299))

    second = holds.hold_slot(db, slot_id, "s2", now=t0 + timedelta(seconds=301))
    db.expire_all()
    assert db.get(Appointment, first.id).status == AppointmentStatus.EXPIRED
    assert second.status == AppointmentStatus.HELD
    assert db.get(Slot, slot_id).held_by == "s2"


def test_sweeper_releases_expired_holds(db, slot_id):
    t0 = utcnow()
    appt = holds.hold_slot(db, slot_id, "s1", ttl_seconds=300, now=t0)

    assert holds.release_expired(db, now=t0 + timedelta(seconds=100)) == 0  # not yet
    assert holds.release_expired(db, now=t0 + timedelta(seconds=301)) == 1

    db.expire_all()
    slot = db.get(Slot, slot_id)
    assert slot.status == SlotStatus.FREE and slot.held_by is None
    assert db.get(Appointment, appt.id).status == AppointmentStatus.EXPIRED
    assert holds.release_expired(db, now=t0 + timedelta(seconds=400)) == 0  # idempotent


def test_cannot_hold_a_slot_in_the_past(db, slot_id):
    start = db.get(Slot, slot_id).start_at
    with pytest.raises(SlotUnavailable):
        holds.hold_slot(db, slot_id, "s1", now=start + timedelta(minutes=1))


def test_confirm_happy_path_then_slot_is_taken(db, slot_id):
    appt = holds.hold_slot(db, slot_id, "s1", service_id="svc_cleaning")
    patient, created = find_or_create_patient(db, "98450 12345", "Asha")
    assert created

    confirmed = holds.confirm_booking(db, appt.id, patient.id, "pay_mock_1")
    assert confirmed.status == AppointmentStatus.CONFIRMED
    assert confirmed.patient_id == patient.id and confirmed.ics_uid

    db.expire_all()
    assert db.get(Slot, slot_id).status == SlotStatus.BOOKED
    with pytest.raises(SlotUnavailable):
        holds.hold_slot(db, slot_id, "s2")

    # Webhook + client callback may both confirm: second call is a no-op.
    again = holds.confirm_booking(db, appt.id, patient.id, "pay_mock_1")
    assert again.status == AppointmentStatus.CONFIRMED


def test_confirm_after_expiry_fails(db, slot_id):
    t0 = utcnow()
    appt = holds.hold_slot(db, slot_id, "s1", ttl_seconds=300, now=t0)
    patient, _ = find_or_create_patient(db, "9845012345", "Asha")
    with pytest.raises(HoldExpired):
        holds.confirm_booking(db, appt.id, patient.id, "pay_1", now=t0 + timedelta(seconds=301))


def test_confirm_after_someone_else_took_over_fails(db, slot_id):
    t0 = utcnow()
    first = holds.hold_slot(db, slot_id, "s1", ttl_seconds=300, now=t0)
    holds.hold_slot(db, slot_id, "s2", now=t0 + timedelta(seconds=301))
    patient, _ = find_or_create_patient(db, "9845012345", "Asha")
    with pytest.raises(HoldExpired):
        holds.confirm_booking(db, first.id, patient.id, "pay_1", now=t0 + timedelta(seconds=302))


def test_unique_index_backstop_blocks_double_active_appointment(db, slot_id):
    """Even if application logic were buggy, the DB itself refuses a second active booking."""
    db.add(Appointment(id=new_id("appt"), slot_id=slot_id, status=AppointmentStatus.HELD, session_id="a"))
    db.commit()
    db.add(Appointment(id=new_id("appt"), slot_id=slot_id, status=AppointmentStatus.CONFIRMED, session_id="b"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # Non-active statuses may coexist with an active one (history is kept).
    db.add(Appointment(id=new_id("appt"), slot_id=slot_id, status=AppointmentStatus.EXPIRED, session_id="c"))
    db.commit()


def test_available_slots_hide_held_and_filter_part_of_day(db, slot_id):
    slot = db.get(Slot, slot_id)
    day = slot.start_at.date()
    start_day, end_day = day - timedelta(days=1), day + timedelta(days=1)

    before = holds.get_available_slots(db, "doc_rao", start_day, end_day)
    assert slot_id in {s.id for s in before}

    holds.hold_slot(db, slot_id, "s1")
    after = holds.get_available_slots(db, "doc_rao", start_day, end_day)
    assert slot_id not in {s.id for s in after}

    mornings = holds.get_available_slots(db, "doc_rao", start_day, end_day, "morning")
    evenings = holds.get_available_slots(db, "doc_rao", start_day, end_day, "evening")
    assert mornings and evenings
    assert {s.id for s in mornings}.isdisjoint({s.id for s in evenings})


def test_audit_log_records_hold_and_rejection(db, slot_id):
    holds.hold_slot(db, slot_id, "s1")
    with pytest.raises(SlotUnavailable):
        holds.hold_slot(db, slot_id, "s2")
    actions = [a for a in db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()]
    assert "slot_held" in actions and "hold_rejected" in actions


@pytest.mark.parametrize(
    "raw, expected",
    [("9845012345", "+919845012345"), ("+91 98450-12345", "+919845012345"), ("09845012345", "+919845012345")],
)
def test_mobile_normalization(raw, expected):
    assert normalize_mobile(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "5845012345", "abcdefghij", ""])
def test_mobile_rejects_garbage(raw):
    with pytest.raises(ValueError):
        normalize_mobile(raw)


def test_find_or_create_patient_is_idempotent(db):
    a, created_a = find_or_create_patient(db, "9845012345", "Asha")
    b, created_b = find_or_create_patient(db, "+91 9845012345")
    assert created_a and not created_b and a.id == b.id
