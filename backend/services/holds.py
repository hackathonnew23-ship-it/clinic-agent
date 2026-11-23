"""Slot availability, atomic holds, expiry and confirmation.

THE LOCKING IDEA
----------------
hold_slot() does ONE conditional UPDATE:

    UPDATE slots SET status='HELD', held_by=:me, held_until=:until
    WHERE id=:slot AND start_at > :now
      AND (status='FREE' OR (status='HELD' AND held_until < :now))

The database applies this atomically, so when N requests race for one slot exactly
one of them sees rowcount == 1. The others see 0 and are rejected. There is no
read-then-write gap, so no explicit row lock is needed (this works on SQLite and
Postgres alike). The partial unique index on appointments is the second line of defence.

Nothing here talks to an LLM: the agent can only reach this code through adapter tools.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment, AppointmentStatus, Doctor, Holiday, Slot, SlotStatus, new_id
from app.timeutil import local_range_to_utc, part_of_day, utc_to_local, utcnow
from services import audit
from services.errors import BookingNotFound, HoldExpired, SlotUnavailable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- availability
def get_available_slots(
    db: Session,
    doctor_id: str,
    date_from: date,
    date_to: date,
    part_of_day_filter: str | None = None,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> list[Slot]:
    """Free slots (including slots whose hold has expired) in a local date range."""
    now = now or utcnow()
    tz_name = tz_name or get_settings().clinic_timezone
    range_start, range_end = local_range_to_utc(date_from, date_to, tz_name)
    range_start = max(range_start, now)

    rows = db.scalars(
        select(Slot)
        .where(
            Slot.doctor_id == doctor_id,
            Slot.start_at >= range_start,
            Slot.start_at < range_end,
            or_(
                Slot.status == SlotStatus.FREE,
                and_(Slot.status == SlotStatus.HELD, Slot.held_until < now),
            ),
        )
        .order_by(Slot.start_at)
    ).all()

    doctor = db.get(Doctor, doctor_id)
    holiday_days: set[date] = set()
    if doctor is not None:
        holiday_days = set(
            db.scalars(
                select(Holiday.day).where(
                    Holiday.clinic_id == doctor.clinic_id,
                    Holiday.day >= date_from,
                    Holiday.day <= date_to,
                )
            ).all()
        )

    result: list[Slot] = []
    for slot in rows:
        local = utc_to_local(slot.start_at, tz_name)
        if local.date() in holiday_days:
            continue
        if part_of_day_filter and part_of_day_filter != "any":
            if part_of_day(local.hour) != part_of_day_filter:
                continue
        result.append(slot)
    return result


# --------------------------------------------------------------------------- hold
def hold_slot(
    db: Session,
    slot_id: str,
    session_id: str,
    ttl_seconds: int | None = None,
    service_id: str | None = None,
    now: datetime | None = None,
) -> Appointment:
    """Atomically hold a slot. Raises SlotUnavailable if somebody else got there first."""
    now = now or utcnow()
    ttl = ttl_seconds if ttl_seconds is not None else get_settings().hold_ttl_seconds
    until = now + timedelta(seconds=ttl)

    try:
        result = db.execute(
            update(Slot)
            .where(
                Slot.id == slot_id,
                Slot.start_at > now,
                or_(
                    Slot.status == SlotStatus.FREE,
                    and_(Slot.status == SlotStatus.HELD, Slot.held_until < now),
                ),
            )
            .values(status=SlotStatus.HELD, held_by=session_id, held_until=until)
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            db.rollback()
            reason = "not_found" if db.get(Slot, slot_id) is None else "unavailable"
            _record_rejection(db, slot_id, session_id, reason, now)
            raise SlotUnavailable(slot_id, reason)

        # We won the slot. If we took over an expired hold, retire the old appointment
        # first so the partial unique index only ever sees one active row.
        db.execute(
            update(Appointment)
            .where(
                Appointment.slot_id == slot_id,
                Appointment.status.in_(AppointmentStatus.IN_FLIGHT),
            )
            .values(status=AppointmentStatus.EXPIRED, updated_at=now)
            .execution_options(synchronize_session=False)
        )

        appointment = Appointment(
            id=new_id("appt"),
            slot_id=slot_id,
            service_id=service_id,
            status=AppointmentStatus.HELD,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        db.add(appointment)
        audit.log(
            db, actor="agent", action="slot_held", entity="appointment",
            entity_id=appointment.id, session_id=session_id, now=now,
            payload={"slot_id": slot_id, "service_id": service_id, "held_until": until.isoformat()},
        )
        db.commit()
        return appointment

    except SlotUnavailable:
        raise
    except IntegrityError:
        # Backstop: the unique index refused a second active appointment for this slot.
        db.rollback()
        _record_rejection(db, slot_id, session_id, "unique_backstop", now)
        raise SlotUnavailable(slot_id, "unique_backstop") from None
    except Exception:
        db.rollback()
        raise


def _record_rejection(db: Session, slot_id: str, session_id: str, reason: str, now: datetime) -> None:
    audit.log(
        db, actor="agent", action="hold_rejected", entity="slot",
        entity_id=slot_id, session_id=session_id, now=now, payload={"reason": reason},
    )
    db.commit()


# --------------------------------------------------------------------------- expiry sweeper
def release_expired(db: Session, now: datetime | None = None) -> int:
    """Release every expired hold. Returns how many slots were freed.

    Also called lazily: hold_slot/get_available_slots already treat expired holds as free,
    so the sweeper is housekeeping that keeps data tidy and the audit log truthful.
    """
    now = now or utcnow()
    try:
        expired_ids = list(
            db.scalars(
                select(Slot.id).where(Slot.status == SlotStatus.HELD, Slot.held_until < now)
            ).all()
        )
        if not expired_ids:
            db.rollback()
            return 0

        db.execute(
            update(Appointment)
            .where(
                Appointment.slot_id.in_(expired_ids),
                Appointment.status.in_(AppointmentStatus.IN_FLIGHT),
            )
            .values(status=AppointmentStatus.EXPIRED, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        freed = db.execute(
            update(Slot)
            .where(
                Slot.id.in_(expired_ids),
                Slot.status == SlotStatus.HELD,
                Slot.held_until < now,
            )
            .values(status=SlotStatus.FREE, held_by=None, held_until=None)
            .execution_options(synchronize_session=False)
        ).rowcount
        audit.log(
            db, actor="system", action="holds_expired", entity="slot",
            now=now, payload={"count": freed, "slot_ids": expired_ids},
        )
        db.commit()
        return int(freed)
    except Exception:
        db.rollback()
        raise


# --------------------------------------------------------------------------- confirm
def confirm_booking(
    db: Session,
    appointment_id: str,
    patient_id: str,
    payment_ref: str,
    now: datetime | None = None,
) -> Appointment:
    """HELD / PAYMENT_PENDING -> CONFIRMED and slot -> BOOKED, in one transaction.

    This function does NOT check payment itself. In Phase 3 only the payment state
    machine calls it, after a verified payment success. It is never exposed to the LLM.
    Idempotent: confirming an already-confirmed appointment for the same patient
    returns it unchanged (webhook + client callback can both arrive).
    """
    now = now or utcnow()
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise BookingNotFound(f"appointment {appointment_id} not found")

    if appointment.status == AppointmentStatus.CONFIRMED:
        if appointment.patient_id == patient_id:
            return appointment
        raise HoldExpired("appointment already confirmed for a different patient")
    if appointment.status not in AppointmentStatus.IN_FLIGHT:
        raise HoldExpired(f"appointment is {appointment.status}, cannot confirm")

    try:
        slot_update = db.execute(
            update(Slot)
            .where(
                Slot.id == appointment.slot_id,
                Slot.status == SlotStatus.HELD,
                Slot.held_by == appointment.session_id,
                Slot.held_until >= now,
            )
            .values(status=SlotStatus.BOOKED, held_until=None)
            .execution_options(synchronize_session=False)
        )
        if slot_update.rowcount != 1:
            db.rollback()
            raise HoldExpired("hold expired or slot was taken")

        appt_update = db.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.status.in_(AppointmentStatus.IN_FLIGHT),
            )
            .values(
                status=AppointmentStatus.CONFIRMED,
                patient_id=patient_id,
                ics_uid=f"{appointment_id}@clinic-agent",
                external_ref=f"CLN-{appointment_id[-8:].upper()}",
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if appt_update.rowcount != 1:
            db.rollback()
            raise HoldExpired("appointment state changed")

        audit.log(
            db, actor="system", action="booking_confirmed", entity="appointment",
            entity_id=appointment_id, session_id=appointment.session_id, now=now,
            payload={"patient_id": patient_id, "payment_ref": payment_ref},
        )
        db.commit()
    except HoldExpired:
        raise
    except Exception:
        db.rollback()
        raise

    db.expire(appointment)
    refreshed = db.get(Appointment, appointment_id)
    assert refreshed is not None
    return refreshed


def get_appointment(db: Session, appointment_id: str) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise BookingNotFound(f"appointment {appointment_id} not found")
    return appointment
