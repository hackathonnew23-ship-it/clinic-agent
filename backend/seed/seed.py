"""Demo data: Smile Dental Clinic, Dr. Rao (+ Dr. Shetty), dental services, 30-minute slots.

    cd backend
    python -m seed.seed --reset      # wipe + reseed
    python -m seed.seed              # keep data, just add any missing future slots

Dr. Rao works Mon-Sat, 09:00-13:00 and 16:00-19:00 (so "Saturday morning" has slots).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_engine, init_engine
from app.models import (
    Base,
    Clinic,
    Doctor,
    DoctorService,
    Faq,
    Holiday,
    Service,
    Slot,
    SlotStatus,
    WorkingHours,
    new_id,
)
from app.timeutil import local_to_utc

CLINIC_ID = "clinic_smile"
SLOT_MINUTES = 30

# Refund tiers, evaluated in Phase 7 (hours before the appointment -> refund percent).
REFUND_RULES = [
    {"min_hours": 24, "refund_percent": 100},
    {"min_hours": 2, "refund_percent": 50},
    {"min_hours": 0, "refund_percent": 0},
]


def _seed_catalog(db: Session) -> None:
    db.add(
        Clinic(
            id=CLINIC_ID,
            name="Smile Dental Clinic",
            timezone=get_settings().clinic_timezone,
            theme_json=json.dumps(
                {"primary": "#0e7c86", "logo": "", "tone": "friendly", "title": "Smile Dental Clinic"}
            ),
            default_lang="en",
            refund_rules_json=json.dumps(REFUND_RULES),
        )
    )
    db.add_all(
        [
            Doctor(id="doc_rao", clinic_id=CLINIC_ID, name="Dr. Rao", specialty="General Dentistry"),
            Doctor(id="doc_shetty", clinic_id=CLINIC_ID, name="Dr. Meera Shetty", specialty="Endodontics"),
        ]
    )
    services = [
        Service(id="svc_consult", clinic_id=CLINIC_ID, name_key="service.consultation",
                name="Consultation", duration_min=30, fee_paise=30000),
        Service(id="svc_cleaning", clinic_id=CLINIC_ID, name_key="service.cleaning",
                name="Teeth Cleaning", duration_min=30, fee_paise=60000),
        Service(id="svc_filling", clinic_id=CLINIC_ID, name_key="service.filling",
                name="Dental Filling", duration_min=30, fee_paise=120000),
        Service(id="svc_rootcanal", clinic_id=CLINIC_ID, name_key="service.root_canal",
                name="Root Canal Treatment", duration_min=60, fee_paise=450000),
    ]
    db.add_all(services)
    db.flush()
    db.add_all(
        [
            DoctorService(doctor_id="doc_rao", service_id="svc_consult"),
            DoctorService(doctor_id="doc_rao", service_id="svc_cleaning"),
            DoctorService(doctor_id="doc_rao", service_id="svc_filling"),
            DoctorService(doctor_id="doc_rao", service_id="svc_rootcanal"),
            DoctorService(doctor_id="doc_shetty", service_id="svc_consult"),
            DoctorService(doctor_id="doc_shetty", service_id="svc_rootcanal", fee_override_paise=550000),
        ]
    )
    for weekday in range(6):  # Mon-Sat
        db.add(WorkingHours(doctor_id="doc_rao", weekday=weekday, start_time=time(9, 0), end_time=time(13, 0)))
        db.add(WorkingHours(doctor_id="doc_rao", weekday=weekday, start_time=time(16, 0), end_time=time(19, 0)))
    for weekday in (0, 2, 4):  # Mon, Wed, Fri
        db.add(WorkingHours(doctor_id="doc_shetty", weekday=weekday, start_time=time(10, 0), end_time=time(14, 0)))
    db.add(Holiday(clinic_id=CLINIC_ID, day=date(2026, 12, 25), reason="Christmas"))
    db.add_all(
        [
            Faq(clinic_id=CLINIC_ID, lang="en", question="What are the clinic timings?",
                answer="Dr. Rao is available Monday to Saturday, 9 AM to 1 PM and 4 PM to 7 PM."),
            Faq(clinic_id=CLINIC_ID, lang="en", question="Where is the clinic?",
                answer="Smile Dental Clinic, 12 MG Road, Bengaluru."),
            Faq(clinic_id=CLINIC_ID, lang="en", question="What is your cancellation policy?",
                answer="Full refund if cancelled more than 24 hours before, 50% between 2 and 24 hours, none under 2 hours."),
        ]
    )


def _seed_slots(db: Session, days: int, today: date | None = None) -> int:
    tz_name = get_settings().clinic_timezone
    today = today or datetime.now(ZoneInfo(tz_name)).date()

    existing = {(r[0], r[1]) for r in db.execute(select(Slot.doctor_id, Slot.start_at)).all()}
    holidays = set(db.scalars(select(Holiday.day)).all())
    hours = db.scalars(select(WorkingHours)).all()
    doctors = db.scalars(select(Doctor).where(Doctor.active.is_(True))).all()

    created = 0
    step = timedelta(minutes=SLOT_MINUTES)
    for doctor in doctors:
        doctor_hours = [h for h in hours if h.doctor_id == doctor.id]
        for offset in range(days):
            day = today + timedelta(days=offset)
            if day in holidays:
                continue
            for wh in (h for h in doctor_hours if h.weekday == day.weekday()):
                cursor = datetime.combine(day, wh.start_time)
                end = datetime.combine(day, wh.end_time)
                while cursor + step <= end:
                    start_utc = local_to_utc(day, cursor.time(), tz_name)
                    if (doctor.id, start_utc) not in existing:
                        db.add(
                            Slot(
                                id=new_id("slot"),
                                doctor_id=doctor.id,
                                start_at=start_utc,
                                end_at=start_utc + step,
                                status=SlotStatus.FREE,
                            )
                        )
                        existing.add((doctor.id, start_utc))
                        created += 1
                    cursor += step
    return created


def seed(db: Session, days: int = 21, today: date | None = None) -> dict[str, int]:
    """Idempotent: creates the catalog once, then adds any missing slots."""
    if db.get(Clinic, CLINIC_ID) is None:
        _seed_catalog(db)
        db.flush()
    created = _seed_slots(db, days, today)
    db.commit()
    return {"slots_created": created}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    parser.add_argument("--days", type=int, default=21, help="how many days of slots to generate")
    args = parser.parse_args()

    init_engine()
    engine = get_engine()
    if args.reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        stats = seed(db, days=args.days)
    finally:
        db.close()
    print(f"Seeded Smile Dental Clinic (Dr. Rao, Dr. Shetty). New slots: {stats['slots_created']}")


if __name__ == "__main__":
    main()
