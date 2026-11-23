"""Patient lookup / creation. Minimum data only: verified mobile + name."""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Patient, new_id
from services import audit

_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def normalize_mobile(raw: str) -> str:
    """'+91 98765-43210' / '09876543210' / '9876543210' -> '+919876543210'. Raises ValueError."""
    digits = re.sub(r"[\s\-()]", "", raw or "")
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _INDIAN_MOBILE.match(digits):
        raise ValueError("invalid Indian mobile number")
    return "+91" + digits


def find_or_create_patient(
    db: Session,
    mobile: str,
    name: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> tuple[Patient, bool]:
    """Return (patient, created). Safe under concurrent calls for the same mobile."""
    normalized = normalize_mobile(mobile)
    clean_name = (name or "").strip()[:80] or None

    patient = db.scalar(select(Patient).where(Patient.mobile == normalized))
    if patient is not None:
        if clean_name and not patient.name:
            patient.name = clean_name
            audit.log(db, actor="system", action="patient_name_set", entity="patient",
                      entity_id=patient.id, session_id=session_id, now=now)
            db.commit()
        return patient, False

    patient = Patient(id=new_id("pat"), mobile=normalized, name=clean_name)
    db.add(patient)
    audit.log(db, actor="system", action="patient_created", entity="patient",
              entity_id=patient.id, session_id=session_id, now=now)
    try:
        db.commit()
    except IntegrityError:
        # Someone created the same mobile between our SELECT and INSERT: use theirs.
        db.rollback()
        existing = db.scalar(select(Patient).where(Patient.mobile == normalized))
        assert existing is not None
        return existing, False
    return patient, True
