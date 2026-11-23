"""Confirmation message + .ics calendar invite. SMS/WhatsApp are mocked: they are
written to backend/outbox/ (messages.log and one .ics per booking)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import Appointment, Slot
from app.timeutil import utcnow
from services import audit
from services.i18n import day_label

IST = ZoneInfo("Asia/Kolkata")
OUTBOX = Path(__file__).resolve().parent.parent / "outbox"
CLINIC_NAME = "Smile Dental Clinic"

TEMPLATES = {
    "en": "Confirmed! {service} with {doctor} on {when}. Paid Rs {amount}. Booking ref {ref}. Calendar invite attached.",
    "hi": "बुकिंग पक्की! {doctor} के साथ {service}, {when}। भुगतान Rs {amount}। रेफ़: {ref}। कैलेंडर आमंत्रण संलग्न।",
    "kn": "ಬುಕಿಂಗ್ ದೃಢವಾಗಿದೆ! {doctor} ಅವರೊಂದಿಗೆ {service}, {when}. ಪಾವತಿ Rs {amount}. ರೆಫ್: {ref}. ಕ್ಯಾಲೆಂಡರ್ ಆಹ್ವಾನ ಲಗತ್ತಿಸಲಾಗಿದೆ.",
}


def send_sms(mobile: str, text: str) -> None:
    """Mock SMS/WhatsApp gateway: append to the outbox log."""
    OUTBOX.mkdir(exist_ok=True)
    with (OUTBOX / "messages.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(IST).isoformat(timespec='seconds')} TO {mobile}: {text}\n")


def send_whatsapp(mobile: str, text: str) -> None:
    """Mock WhatsApp gateway: append one JSON line to outbox/whatsapp_mock.jsonl."""
    import json
    from datetime import datetime, timezone
    OUTBOX.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "channel": "whatsapp_mock", "to": mobile, "text": text}
    with open(OUTBOX / "whatsapp_mock.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _utc(dt: datetime) -> datetime:
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(uid: str, start: datetime, end: datetime, summary: str, description: str) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Clinic Agent//EN", "METHOD:PUBLISH", "BEGIN:VEVENT",
        f"UID:{uid}@clinic-agent", f"DTSTAMP:{utcnow().strftime(fmt)}",
        f"DTSTART:{_utc(start).strftime(fmt)}", f"DTEND:{_utc(end).strftime(fmt)}",
        f"SUMMARY:{_esc(summary)}", f"LOCATION:{_esc(CLINIC_NAME)}", f"DESCRIPTION:{_esc(description)}",
        "BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", "DESCRIPTION:Appointment reminder", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def send_confirmation(
    db: Session, *, appointment_id: str, mobile: str, start_at: datetime, doctor_name: str,
    service_name: str, amount_paise: int, language: str, session_id: str,
) -> dict:
    appt = db.get(Appointment, appointment_id)
    slot = db.get(Slot, appt.slot_id) if appt else None
    end_at = slot.end_at if slot else _utc(start_at) + timedelta(minutes=30)
    start_local = _utc(start_at).astimezone(IST)
    when = f"{day_label(start_local.strftime('%Y-%m-%d'), language)}, {start_local.strftime('%I:%M %p').lstrip('0')}"
    ref = appointment_id[-8:].upper()

    ics = build_ics(appointment_id, start_at, end_at, f"{service_name} with {doctor_name}",
                    f"{CLINIC_NAME}. Booking ref {ref}.")
    OUTBOX.mkdir(exist_ok=True)
    ics_path = OUTBOX / f"appointment-{ref}.ics"
    ics_path.write_text(ics, encoding="utf-8")

    text = TEMPLATES.get(language, TEMPLATES["en"]).format(
        service=service_name, doctor=doctor_name, when=when, amount=amount_paise // 100, ref=ref)
    send_sms(mobile, text + f" [invite: {ics_path.name}]")
    send_whatsapp(mobile, text + f" [invite: {ics_path.name}]")
    audit.log(db, actor="system", action="confirmation_sent", entity="appointment", entity_id=appointment_id,
              session_id=session_id, payload={"channel": "sms_mock", "ics": ics_path.name})
    db.commit()
    return {"message": text, "ics_path": str(ics_path), "ref": ref}
