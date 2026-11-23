"""Admin API + page: doctors, services, fees, timings, holidays, FAQs.
Auth: header X-Admin-Token must equal ADMIN_PASSWORD from .env (dev default "admin123")."""
from __future__ import annotations

import os
import re
import secrets
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Clinic, Doctor, DoctorService, Faq, Holiday, Service, WorkingHours, new_id
from services.slots import regenerate_slots

STATIC = Path(__file__).resolve().parent.parent.parent / "static"
CLINIC_ID = "clinic_smile"


def _auth(x_admin_token: str = Header(default="")) -> None:
    get_settings()  # makes sure .env is loaded
    password = os.getenv("ADMIN_PASSWORD", "admin123")
    if not secrets.compare_digest(x_admin_token.encode(), password.encode()):
        raise HTTPException(status_code=401, detail="Wrong admin password")


api = APIRouter(prefix="/admin/api", dependencies=[Depends(_auth)])
pages = APIRouter()


class DoctorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    specialty: str = Field(default="", max_length=80)
    active: bool = True
    service_ids: list[str] = []


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    fee_rupees: float = Field(ge=0, le=1_000_000)
    duration_min: int = Field(ge=5, le=480)
    active: bool = True


class HoursIn(BaseModel):
    doctor_id: str
    weekday: int = Field(ge=0, le=6)
    start: str
    end: str


class HolidayIn(BaseModel):
    day: str
    reason: str = Field(default="", max_length=200)


class FaqIn(BaseModel):
    lang: str = Field(default="en", pattern="^(en|hi|kn|tu)$")
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=4000)


def _get(db, model, pk):
    row = db.get(model, pk)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


def _time(text: str):
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail="time must be HH:MM")


def _sync_services(db, doctor_id: str, service_ids: list[str]) -> None:
    existing = {r.service_id: r for r in db.scalars(select(DoctorService).where(DoctorService.doctor_id == doctor_id))}
    valid = set(db.scalars(select(Service.id)).all())
    want = {s for s in service_ids if s in valid}
    for sid, row in existing.items():
        if sid not in want:
            db.delete(row)
    for sid in want - set(existing):
        db.add(DoctorService(doctor_id=doctor_id, service_id=sid))


@api.get("/overview")
def overview() -> dict:
    with SessionLocal() as db:
        clinic = db.get(Clinic, CLINIC_ID)
        docs = db.scalars(select(Doctor).where(Doctor.clinic_id == CLINIC_ID).order_by(Doctor.name)).all()
        svcs = db.scalars(select(Service).where(Service.clinic_id == CLINIC_ID).order_by(Service.name)).all()
        links = db.scalars(select(DoctorService)).all()
        names = {d.id: d.name for d in docs}
        hours = db.scalars(select(WorkingHours).order_by(WorkingHours.doctor_id, WorkingHours.weekday, WorkingHours.start_time)).all()
        holidays = db.scalars(select(Holiday).where(Holiday.clinic_id == CLINIC_ID).order_by(Holiday.day)).all()
        faqs = db.scalars(select(Faq).where(Faq.clinic_id == CLINIC_ID).order_by(Faq.lang, Faq.id)).all()
        return {
            "clinic": {"id": CLINIC_ID, "name": clinic.name if clinic else "Clinic"},
            "doctors": [
                {"id": d.id, "name": d.name, "specialty": d.specialty, "active": d.active,
                 "service_ids": [l.service_id for l in links if l.doctor_id == d.id]}
                for d in docs
            ],
            "services": [
                {"id": s.id, "name": s.name, "fee_rupees": s.fee_paise / 100, "duration_min": s.duration_min, "active": s.active}
                for s in svcs
            ],
            "hours": [
                {"id": h.id, "doctor_id": h.doctor_id, "doctor_name": names[h.doctor_id], "weekday": h.weekday,
                 "start": h.start_time.strftime("%H:%M"), "end": h.end_time.strftime("%H:%M")}
                for h in hours if h.doctor_id in names
            ],
            "holidays": [{"id": h.id, "day": h.day.isoformat(), "reason": h.reason} for h in holidays],
            "faqs": [{"id": f.id, "lang": f.lang, "question": f.question, "answer": f.answer} for f in faqs],
        }


@api.post("/doctor")
def add_doctor(body: DoctorIn) -> dict:
    with SessionLocal() as db:
        row = Doctor(id=new_id("doc"), clinic_id=CLINIC_ID, name=body.name.strip(), specialty=body.specialty.strip(), active=body.active)
        db.add(row)
        db.flush()
        _sync_services(db, row.id, body.service_ids)
        db.commit()
        return {"id": row.id}


@api.put("/doctor/{doctor_id}")
def edit_doctor(doctor_id: str, body: DoctorIn) -> dict:
    with SessionLocal() as db:
        row = _get(db, Doctor, doctor_id)
        row.name, row.specialty, row.active = body.name.strip(), body.specialty.strip(), body.active
        _sync_services(db, row.id, body.service_ids)
        db.commit()
    return {"ok": True}


@api.post("/service")
def add_service(body: ServiceIn) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_") or "service"
    with SessionLocal() as db:
        row = Service(id=new_id("svc"), clinic_id=CLINIC_ID, name_key="service." + slug, name=body.name.strip(),
                      duration_min=body.duration_min, fee_paise=int(round(body.fee_rupees * 100)), active=body.active)
        db.add(row)
        db.commit()
        return {"id": row.id}


@api.put("/service/{service_id}")
def edit_service(service_id: str, body: ServiceIn) -> dict:
    with SessionLocal() as db:
        row = _get(db, Service, service_id)
        row.name, row.duration_min, row.active = body.name.strip(), body.duration_min, body.active
        row.fee_paise = int(round(body.fee_rupees * 100))
        db.commit()
    return {"ok": True}


@api.post("/hours")
def add_hours(body: HoursIn) -> dict:
    start, end = _time(body.start), _time(body.end)
    if start >= end:
        raise HTTPException(status_code=400, detail="end time must be after start time")
    with SessionLocal() as db:
        _get(db, Doctor, body.doctor_id)
        row = WorkingHours(doctor_id=body.doctor_id, weekday=body.weekday, start_time=start, end_time=end)
        db.add(row)
        regenerate_slots(db)
        db.commit()
        return {"id": row.id}


@api.delete("/hours/{hours_id}")
def delete_hours(hours_id: int) -> dict:
    with SessionLocal() as db:
        db.delete(_get(db, WorkingHours, hours_id))
        regenerate_slots(db)
        db.commit()
    return {"ok": True}


@api.post("/holiday")
def add_holiday(body: HolidayIn) -> dict:
    try:
        day = date.fromisoformat(body.day)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    with SessionLocal() as db:
        row = Holiday(clinic_id=CLINIC_ID, day=day, reason=body.reason.strip())
        db.add(row)
        regenerate_slots(db)
        db.commit()
        return {"id": row.id}


@api.delete("/holiday/{holiday_id}")
def delete_holiday(holiday_id: int) -> dict:
    with SessionLocal() as db:
        db.delete(_get(db, Holiday, holiday_id))
        regenerate_slots(db)
        db.commit()
    return {"ok": True}


@api.post("/faq")
def add_faq(body: FaqIn) -> dict:
    with SessionLocal() as db:
        row = Faq(clinic_id=CLINIC_ID, lang=body.lang, question=body.question.strip(), answer=body.answer.strip())
        db.add(row)
        db.commit()
        return {"id": row.id}


@api.put("/faq/{faq_id}")
def edit_faq(faq_id: int, body: FaqIn) -> dict:
    with SessionLocal() as db:
        row = _get(db, Faq, faq_id)
        row.lang, row.question, row.answer = body.lang, body.question.strip(), body.answer.strip()
        db.commit()
    return {"ok": True}


@api.delete("/faq/{faq_id}")
def delete_faq(faq_id: int) -> dict:
    with SessionLocal() as db:
        db.delete(_get(db, Faq, faq_id))
        db.commit()
    return {"ok": True}


@pages.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return (STATIC / "admin.html").read_text(encoding="utf-8")


router = APIRouter()
router.include_router(api)
router.include_router(pages)
