"""Database schema (SQLAlchemy 2.0). Postgres-ready: no SQLite-only types.

All datetimes are naive UTC. Money is integer paise.
Slot safety lives in two places:
  1. holds.py  -> atomic conditional UPDATE on the slot row (only one caller gets rowcount 1)
  2. this file -> partial UNIQUE index so at most ONE active appointment can exist per slot
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SlotStatus:
    FREE = "FREE"
    HELD = "HELD"
    BOOKED = "BOOKED"


class AppointmentStatus:
    HELD = "HELD"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"

    # These statuses occupy the slot (see the partial unique index below).
    ACTIVE = (HELD, PAYMENT_PENDING, CONFIRMED)
    # Statuses where a hold exists but the booking is not final yet.
    IN_FLIGHT = (HELD, PAYMENT_PENDING)


# --------------------------------------------------------------------------- catalog
class Clinic(Base):
    __tablename__ = "clinics"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Kolkata")
    theme_json: Mapped[str] = mapped_column(Text, default="{}")
    default_lang: Mapped[str] = mapped_column(String(5), default="en")
    # JSON list of {"min_hours": float, "refund_percent": int}, evaluated in Phase 7.
    refund_rules_json: Mapped[str] = mapped_column(Text, default="[]")


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Service(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    name_key: Mapped[str] = mapped_column(String(60))  # i18n key, e.g. "service.cleaning"
    name: Mapped[str] = mapped_column(String(120))  # English display name (fallback)
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    fee_paise: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DoctorService(Base):
    __tablename__ = "doctor_services"
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.id"), primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), primary_key=True)
    fee_override_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WorkingHours(Base):
    __tablename__ = "working_hours"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.id"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday ... 6 = Sunday
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class Holiday(Base):
    __tablename__ = "holidays"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    day: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(200), default="")


class Faq(Base):
    __tablename__ = "faqs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)


# --------------------------------------------------------------------------- bookings
class Slot(Base):
    __tablename__ = "slots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("doctors.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default=SlotStatus.FREE)
    held_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # chat session id
    held_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # A doctor cannot have two slots starting at the same time.
        UniqueConstraint("doctor_id", "start_at", name="uq_slot_doctor_start"),
        Index("ix_slots_lookup", "doctor_id", "start_at", "status"),
    )


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mobile: Mapped[str] = mapped_column(String(16), unique=True)  # normalized +91XXXXXXXXXX
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    service_id: Mapped[str | None] = mapped_column(ForeignKey("services.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=AppointmentStatus.HELD)
    session_id: Mapped[str] = mapped_column(String(64))
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ics_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        # BACKSTOP: even if application logic had a bug, the database refuses a second
        # active appointment for the same slot. Works on SQLite (3.8+) and Postgres.
        Index(
            "uq_active_appointment_per_slot",
            "slot_id",
            unique=True,
            sqlite_where=text("status IN ('HELD','PAYMENT_PENDING','CONFIRMED')"),
            postgresql_where=text("status IN ('HELD','PAYMENT_PENDING','CONFIRMED')"),
        ),
    )


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"))
    provider: Mapped[str] = mapped_column(String(20))  # mock | razorpay
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------- chat / compliance
class OtpSession(Base):
    __tablename__ = "otp_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64))
    mobile: Mapped[str] = mapped_column(String(16))
    code_hash: Mapped[str] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    text_version: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(5))  # language the consent text was shown in
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    state: Mapped[str] = mapped_column(String(30), default="START")
    slot_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    appointment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    transcript_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(20))  # patient | agent | system | admin
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
