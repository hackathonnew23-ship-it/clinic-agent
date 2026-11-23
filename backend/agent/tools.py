"""Booking tools the LLM can call. Slots and holds go ONLY through the BookingProvider.
Phase 3 adds consent, OTP and payment tools. The LLM can never mark a payment as paid:
complete_payment() is called by the app (the 'pay' button), not exposed as a tool."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from adapters.registry import get_provider
from app.db import SessionLocal
from app.models import Slot
from services import confirmation, consent, i18n, otp, payments
from services.errors import BookingError
from services.flow_errors import FlowError

IST = ZoneInfo("Asia/Kolkata")

DOCTORS = {
    "doc_rao": {"name": "Dr. Rao", "services": ["svc_consult", "svc_cleaning", "svc_filling", "svc_rootcanal"]},
    "doc_shetty": {"name": "Dr. Shetty", "services": ["svc_consult", "svc_rootcanal"]},
}
SERVICES = {
    "svc_consult": "Consultation",
    "svc_cleaning": "Teeth cleaning",
    "svc_filling": "Filling",
    "svc_rootcanal": "Root canal",
}
_SERVICE_ENUM = ["svc_consult", "svc_cleaning", "svc_filling", "svc_rootcanal"]
_DOCTOR_ENUM = list(DOCTORS)
FEES: dict[str, dict[str, int]] = {}  # doctor_id -> {service_id: fee_paise}


def refresh_catalog() -> None:
    """Reload doctors, services and fees from the DB into the existing dicts (admin edits reach the chat)."""
    try:
        from sqlalchemy import select
        from app.models import Doctor, DoctorService, Service
        with SessionLocal() as db:
            svcs = {s.id: s for s in db.scalars(select(Service).where(Service.active == True))}  # noqa: E712
            docs = {d.id: d for d in db.scalars(select(Doctor).where(Doctor.active == True))}  # noqa: E712
            links = db.scalars(select(DoctorService)).all()
        new_docs: dict[str, dict] = {}
        new_fees: dict[str, dict[str, int]] = {}
        for did, doc in docs.items():
            sl, fees = [], {}
            for ln in links:
                if ln.doctor_id == did and ln.service_id in svcs:
                    sl.append(ln.service_id)
                    fees[ln.service_id] = ln.fee_override_paise if ln.fee_override_paise is not None else svcs[ln.service_id].fee_paise
            new_docs[did] = {"name": doc.name, "services": sl}
            new_fees[did] = fees
        if not new_docs or not svcs:
            return
        DOCTORS.clear(); DOCTORS.update(new_docs)
        SERVICES.clear(); SERVICES.update({sid: s.name for sid, s in svcs.items()})
        FEES.clear(); FEES.update(new_fees)
        _SERVICE_ENUM[:] = list(SERVICES)
        _DOCTOR_ENUM[:] = list(DOCTORS)
    except Exception:
        pass  # keep the previous values


def catalog_text() -> str:
    out = []
    for did, info in DOCTORS.items():
        parts = []
        for sid in info["services"]:
            fee = FEES.get(did, {}).get(sid)
            parts.append(f"{sid} ({SERVICES.get(sid, sid)}" + (f", Rs {fee // 100}" if fee is not None else "") + ")")
        out.append(f"- {did} ({info['name']}): " + ", ".join(parts))
    return "\n".join(out)




def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _fmt(slot) -> dict[str, Any]:
    local = _local(slot.start_at)
    return {
        "slot_id": slot.id,
        "doctor_id": slot.doctor_id,
        "date": local.date().isoformat(),
        "weekday": local.strftime("%A"),
        "time": local.strftime("%I:%M %p").lstrip("0"),
    }


def _part_of_day_ok(slot, part: str | None) -> bool:
    if not part:
        return True
    hour = _local(slot.start_at).hour
    if part == "morning":
        return hour < 12
    if part == "afternoon":
        return 12 <= hour < 17
    if part == "evening":
        return hour >= 17
    return True


class BookingTools:
    def __init__(self, provider=None, session_id: str = "cli-session") -> None:
        self.provider = provider or get_provider()
        self.session_id = session_id
        self.last_slots: list[dict[str, Any]] = []
        self.notices: list[str] = []  # test-mode messages the app shows (e.g. the OTP SMS)
        self._reset_flow()

    def _reset_flow(self) -> None:
        self.current_hold = None
        self.service_id: str | None = None
        self.language = "en"
        self.ui_lang: str | None = None  # language picked in the widget (hi/kn/tu)
        self.consent_shown = False
        self.consent_id: str | None = None
        self.otp_sent = False
        self.patient = None
        self.payment_id: str | None = None
        self.booking = None
        self._manage = None

    @property
    def payment_pending(self) -> bool:
        return self.payment_id is not None and self.booking is None

    declarations = [
        {
            "name": "search_knowledge",
            "description": "Look up general clinic information (parking, payment, insurance, policies, what to bring) from the clinic's own documents. Use for any non-booking clinic question. Never use for medical questions.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "The patient's question in English keywords"}}, "required": ["query"]},
        },
        {
            "name": "handover_to_staff",
            "description": "Hand the conversation to clinic staff. Use when the patient asks for a human, is upset, asks something you cannot answer from tools, or a tool keeps failing. Never guess instead.",
            "parameters": {"type": "OBJECT", "properties": {"reason": {"type": "STRING", "description": "Short reason for staff"}}, "required": ["reason"]},
        },
        {
            "name": "start_manage",
            "description": "Start cancelling or rescheduling an existing CONFIRMED booking. Needs the booking reference from the patient's confirmation message. Sends an OTP to the mobile on file.",
            "parameters": {"type": "OBJECT", "properties": {"booking_ref": {"type": "STRING"}}, "required": ["booking_ref"]},
        },
        {
            "name": "verify_manage_otp",
            "description": "Check the 6-digit code for managing an existing booking. Do not show booking details before this succeeds.",
            "parameters": {"type": "OBJECT", "properties": {"code": {"type": "STRING"}}, "required": ["code"]},
        },
        {
            "name": "quote_cancel",
            "description": "After OTP success: get the exact refund the patient would receive if they cancel. Tell them the amount and ask yes/no.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "cancel_booking",
            "description": "Cancel the booking. ONLY after quote_cancel was shown to the patient and they clearly said yes. Pass patient_confirmed=true.",
            "parameters": {"type": "OBJECT", "properties": {"patient_confirmed": {"type": "BOOLEAN"}}, "required": ["patient_confirmed"]},
        },
        {
            "name": "list_reschedule_slots",
            "description": "After OTP success: list free new slots with the same doctor (same service and fee). Never invent slots.",
            "parameters": {"type": "OBJECT", "properties": {"date_from": {"type": "STRING", "description": "YYYY-MM-DD, optional"}}},
        },
        {
            "name": "reschedule_booking",
            "description": "Move the booking to a slot returned by list_reschedule_slots. ONLY after the patient clearly chose that slot and said yes. Pass patient_confirmed=true.",
            "parameters": {"type": "OBJECT", "properties": {"new_slot_id": {"type": "STRING"}, "patient_confirmed": {"type": "BOOLEAN"}}, "required": ["new_slot_id", "patient_confirmed"]},
        },
        {
            "name": "start_manage",
            "description": "Start cancelling or rescheduling an existing CONFIRMED booking. Needs the booking reference from the patient's confirmation message. Sends an OTP to the mobile on file.",
            "parameters": {"type": "OBJECT", "properties": {"booking_ref": {"type": "STRING"}}, "required": ["booking_ref"]},
        },
        {
            "name": "verify_manage_otp",
            "description": "Check the 6-digit code for managing an existing booking. Do not show booking details before this succeeds.",
            "parameters": {"type": "OBJECT", "properties": {"code": {"type": "STRING"}}, "required": ["code"]},
        },
        {
            "name": "quote_cancel",
            "description": "After OTP success: get the exact refund the patient would receive if they cancel. Tell them the amount and ask yes/no.",
        },
        {
            "name": "cancel_booking",
            "description": "Cancel the booking. ONLY after quote_cancel was shown to the patient and they clearly said yes. Pass patient_confirmed=true.",
            "parameters": {"type": "OBJECT", "properties": {"patient_confirmed": {"type": "BOOLEAN"}}, "required": ["patient_confirmed"]},
        },
        {
            "name": "list_reschedule_slots",
            "description": "After OTP success: list free new slots with the same doctor (same service and fee). Never invent slots.",
            "parameters": {"type": "OBJECT", "properties": {"date_from": {"type": "STRING", "description": "YYYY-MM-DD, optional"}}},
        },
        {
            "name": "reschedule_booking",
            "description": "Move the booking to a slot returned by list_reschedule_slots. ONLY after the patient clearly chose that slot and said yes. Pass patient_confirmed=true.",
            "parameters": {"type": "OBJECT", "properties": {"new_slot_id": {"type": "STRING"}, "patient_confirmed": {"type": "BOOLEAN"}}, "required": ["new_slot_id", "patient_confirmed"]},
        },
        {
            "name": "get_available_slots",
            "description": "List real free appointment slots for a doctor and service. Use this before offering any time. Never invent slots.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "doctor_id": {"type": "STRING", "enum": _DOCTOR_ENUM},
                    "service_id": {"type": "STRING", "enum": _SERVICE_ENUM},
                    "date_from": {"type": "STRING", "description": "YYYY-MM-DD, first day to search"},
                    "date_to": {"type": "STRING", "description": "YYYY-MM-DD, last day. Same as date_from for one day"},
                    "part_of_day": {"type": "STRING", "enum": ["morning", "afternoon", "evening"]},
                },
                "required": ["doctor_id", "service_id", "date_from"],
            },
        },
        {
            "name": "hold_slot",
            "description": (
                "Temporarily hold the slot the patient chose for 5 minutes. Only call after the patient clearly "
                "picks one slot. Returns the consent text to show the patient."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "slot_id": {"type": "STRING", "description": "slot_id exactly as returned by get_available_slots"},
                    "service_id": {"type": "STRING", "enum": _SERVICE_ENUM},
                    "language": {"type": "STRING", "enum": ["en", "hi", "kn", "tu"], "description": "Patient's language"},
                },
                "required": ["slot_id", "service_id"],
            },
        },
        {
            "name": "record_consent",
            "description": "Record the patient's explicit consent. Call ONLY after the patient clearly says they agree to the consent text.",
            "parameters": {"type": "OBJECT", "properties": {"language": {"type": "STRING", "enum": ["en", "hi", "kn", "tu"]}}},
        },
        {
            "name": "send_otp",
            "description": "Send a verification OTP to the patient's mobile number. Needs consent first.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"mobile": {"type": "STRING", "description": "10-digit Indian mobile number"}},
                "required": ["mobile"],
            },
        },
        {
            "name": "verify_otp",
            "description": "Check the 6-digit code the patient typed. On success the payment is created.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"code": {"type": "STRING", "description": "6-digit code typed by the patient"}},
                "required": ["code"],
            },
        },
    ]
    declarations = list({d["name"]: d for d in declarations}.values())

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name in {d["name"] for d in self.declarations}:
                return getattr(self, name)(**args)
            return {"error": "unknown_tool", "message": f"No tool named {name}"}
        except (BookingError, FlowError) as exc:
            return {"error": exc.code, "message": str(exc), **getattr(exc, "extra", {})}
        except (TypeError, ValueError) as exc:
            return {"error": "bad_arguments", "message": str(exc)}

    # ------------------------------------------------------------------ slots
    def get_available_slots(
        self,
        doctor_id: str,
        service_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
        part_of_day: str | None = None,
    ) -> dict[str, Any]:
        if doctor_id not in DOCTORS:
            return {"error": "unknown_doctor", "message": f"Doctors: {list(DOCTORS)}"}
        if service_id not in DOCTORS[doctor_id]["services"]:
            return {
                "error": "service_not_offered",
                "message": f"{DOCTORS[doctor_id]['name']} does not offer {SERVICES.get(service_id, service_id)}.",
            }
        start = date.fromisoformat(date_from) if date_from else datetime.now(IST).date()
        end = date.fromisoformat(date_to) if date_to else start
        slots = self.provider.get_slots(doctor_id, service_id, start, end)
        slots = sorted((s for s in slots if _part_of_day_ok(s, part_of_day)), key=lambda s: s.start_at)
        self.last_slots = [_fmt(s) for s in slots[:8]]
        if not self.last_slots:
            return {"slots": [], "note": "No free slots in that window. Offer another day or time."}
        return {"slots": self.last_slots, "count": len(self.last_slots)}

    def hold_slot(self, slot_id: str, service_id: str, language: str = "en") -> dict[str, Any]:
        hold = self.provider.hold_slot(slot_id, self.session_id, 300, service_id)
        keep_ui = self.ui_lang
        lang = consent.norm_lang(keep_ui or language)  # read BEFORE the reset can clear it
        self._reset_flow()
        self.ui_lang = keep_ui
        self.current_hold = hold
        self.service_id = service_id
        self.language = lang
        self.consent_shown = True
        return {
            "status": "held",
            "hold_id": hold.hold_id,
            "slot_id": hold.slot_id,
            "held_until": _local(hold.expires_at).strftime("%I:%M %p").lstrip("0"),
            "minutes": 5,
            "consent_text": consent.get_consent_text(self.language),
            "instruction": "Show consent_text, then ask the patient to reply 'I agree' with their 10-digit mobile number.",
        }

    # ------------------------------------------------------------------ consent / otp
    def record_consent(self, language: str | None = None) -> dict[str, Any]:
        if self.current_hold is None or not self.consent_shown:
            raise FlowError("no_hold", "Pick and hold a slot first.")
        with SessionLocal() as db:
            row = consent.record_consent(db, self.session_id, self.ui_lang or language or self.language)
        self.consent_id = row.id
        return {"status": "consent_recorded", "granted_at": row.granted_at.isoformat() + "Z"}

    def send_otp(self, mobile: str) -> dict[str, Any]:
        if self.current_hold is None:
            raise FlowError("no_hold", "Pick and hold a slot first.")
        if self.consent_id is None:
            raise FlowError("consent_required", "The patient must agree to the consent text first.")
        with SessionLocal() as db:
            res = otp.send_otp(db, self.session_id, mobile)
        self.otp_sent = True
        self.notices.append(f"[test mode] SMS to {res['masked']}: your OTP is {res['test_code']}")
        return {"status": "otp_sent", "mobile_masked": res["masked"], "expires_minutes": 5}

    def verify_otp(self, code: str) -> dict[str, Any]:
        if not self.otp_sent or self.current_hold is None:
            raise FlowError("no_otp", "No OTP was requested yet.")
        with SessionLocal() as db:
            mobile = otp.verify_otp(db, self.session_id, code)
        self.patient = self.provider.find_or_create_patient(mobile)
        with SessionLocal() as db:
            pay = payments.start_payment(db, self.current_hold.hold_id, self.session_id)
        self.payment_id = pay.id
        return {
            "status": "verified",
            "payment": "pending",
            "amount_rupees": pay.amount_paise // 100,
            "instruction": (
                "Tell the patient the fee and that they can now pay (test mode: they type 'pay'; "
                "'fail' simulates a failed payment). Do NOT say the booking is confirmed."
            ),
        }

    # ------------------------------------------------------------------ manage booking (AI tools)
    def _need_verified(self) -> dict:
        m = self._manage
        if not m or not m.get("verified"):
            raise FlowError("not_verified", "Verify the booking with an OTP first.")
        return m

    def start_manage(self, booking_ref: str) -> dict[str, Any]:
        from app.models import Patient
        from agent.cancel_flow import _find
        with SessionLocal() as db:
            appt = _find(db, booking_ref or "")
            if appt is None:
                raise FlowError("not_found", "No confirmed booking found with that reference.")
            patient = db.get(Patient, appt.patient_id) if appt.patient_id else None
            if patient is None or not getattr(patient, "mobile", None):
                raise FlowError("cannot_verify", "Cannot verify this booking automatically. Hand over to staff.")
            res = otp.send_otp(db, self.session_id, patient.mobile)
            appt_id = appt.id
        self._manage = {"appt_id": appt_id, "verified": False, "quoted": False, "offered": []}
        self.notices.append(f"[test mode] SMS to {res['masked']}: your OTP is {res['test_code']}")
        return {"status": "otp_sent", "mobile_masked": res["masked"],
                "instruction": "Ask the patient to type the 6-digit code. Do not reveal booking details yet."}

    def verify_manage_otp(self, code: str) -> dict[str, Any]:
        if not self._manage:
            raise FlowError("no_manage", "Start with the booking reference first.")
        with SessionLocal() as db:
            otp.verify_otp(db, self.session_id, code)
        self._manage["verified"] = True
        return {"status": "verified",
                "instruction": "Ask: cancel or reschedule? Cancel: call quote_cancel. Reschedule: call list_reschedule_slots."}

    def quote_cancel(self) -> dict[str, Any]:
        from sqlalchemy import select
        from app.models import Appointment, Clinic, Payment, Slot
        from app.timeutil import utcnow
        from services.refunds import refund_amount
        m = self._need_verified()
        with SessionLocal() as db:
            appt = db.get(Appointment, m["appt_id"])
            slot = db.get(Slot, appt.slot_id)
            pay = db.scalars(select(Payment).where(
                Payment.appointment_id == appt.id, Payment.status.not_in(["CREATED", "FAILED"]))).first()
            clinic = db.get(Clinic, "clinic_smile")
            pct, refund = refund_amount(pay.amount_paise if pay else 0, slot.start_at, utcnow(),
                                        clinic.refund_rules_json if clinic else None)
        m["quoted"] = True
        return {"refund_percent": pct, "refund_rupees": round(refund / 100, 2), "refund_display": f"Rs {refund / 100:.2f}",
                "instruction": "Tell the patient this exact refund and ask yes/no. Only after a clear yes call cancel_booking."}

    def cancel_booking(self, patient_confirmed: bool = False) -> dict[str, Any]:
        from services.cancellation import cancel_appointment
        m = self._need_verified()
        if not m.get("quoted"):
            raise FlowError("quote_first", "Call quote_cancel and tell the patient the refund first.")
        if patient_confirmed is not True:
            raise FlowError("confirmation_required", "Ask the patient to confirm yes/no first.")
        with SessionLocal() as db:
            try:
                r = cancel_appointment(db, m["appt_id"], "patient request via chat")
            except (LookupError, ValueError):
                raise FlowError("not_cancellable", "This booking cannot be cancelled.")
        self._manage = None
        return {"status": "cancelled", "refund_percent": r["refund_percent"],
                "refund_rupees": round(r["refund_paise"] / 100, 2), "refund_display": f"Rs {r['refund_paise'] / 100:.2f}", "paid_display": f"Rs {r['paid_paise'] / 100:.2f}"}

    def list_reschedule_slots(self, date_from: str | None = None) -> dict[str, Any]:
        from datetime import timedelta
        from sqlalchemy import select
        from app.models import Appointment, Slot, SlotStatus
        from app.timeutil import utcnow
        m = self._need_verified()
        with SessionLocal() as db:
            appt = db.get(Appointment, m["appt_id"])
            cur = db.get(Slot, appt.slot_id)
            rows = db.scalars(select(Slot).where(
                Slot.doctor_id == cur.doctor_id, Slot.status == SlotStatus.FREE,
                Slot.start_at > utcnow() + timedelta(hours=2)).order_by(Slot.start_at).limit(60)).all()
            out = []
            for s in rows:
                loc = _local(s.start_at)
                if date_from and loc.date().isoformat() < date_from:
                    continue
                out.append({"slot_id": s.id, "when": loc.strftime("%a %d %b, %I:%M %p")})
                if len(out) == 8:
                    break
        m["offered"] = [o["slot_id"] for o in out]
        return {"slots": out, "note": "Same doctor, same service, same fee."}

    def reschedule_booking(self, new_slot_id: str, patient_confirmed: bool = False) -> dict[str, Any]:
        from app.models import Slot
        from services.rescheduling import reschedule_appointment
        m = self._need_verified()
        if new_slot_id not in m.get("offered", []):
            raise FlowError("not_offered", "Pick a slot from list_reschedule_slots.")
        if patient_confirmed is not True:
            raise FlowError("confirmation_required", "Ask the patient to confirm yes/no first.")
        with SessionLocal() as db:
            try:
                reschedule_appointment(db, m["appt_id"], new_slot_id)
            except LookupError:
                raise FlowError("not_found", "Booking not found.")
            except ValueError:
                raise FlowError("not_reschedulable", "Cannot reschedule: the appointment is less than 2 hours away or not active.")
            except RuntimeError:
                m["offered"] = []
                raise FlowError("slot_taken", "That slot was just taken. Call list_reschedule_slots again.")
            when = _local(db.get(Slot, new_slot_id).start_at).strftime("%a %d %b, %I:%M %p")
        self._manage = None
        return {"status": "rescheduled", "new_time": when}

    # ------------------------------------------------------------------ staff hand-over (AI tool)
    def handover_to_staff(self, reason: str = "") -> dict[str, Any]:
        from app.models import Handoff, new_id
        from services import audit
        if getattr(self, "_handed_over", False):
            return {"status": "already_handed_over", "instruction": "Tell the patient staff will contact them soon."}
        with SessionLocal() as db:
            h = Handoff(id=new_id("hand"), session_id=self.session_id, reason=(reason or "unspecified")[:500])
            db.add(h)
            audit.log(db, actor="agent", action="handoff_to_staff", entity="handoff", entity_id=h.id,
                      session_id=self.session_id, payload={"reason": reason[:500]})
            db.commit()
        self._handed_over = True
        return {"status": "handed_over",
                "instruction": "Tell the patient a staff member will follow up. Do not promise a time. Do not continue booking."}

    # ------------------------------------------------------------------ clinic knowledge (AI tool)
    def search_knowledge(self, query: str) -> dict[str, Any]:
        from services.knowledge import search
        hits = search(query or "")
        if not hits:
            return {"status": "not_found",
                    "instruction": "This is not in the clinic information. Do NOT guess. Call handover_to_staff."}
        return {"status": "found", "passages": hits,
                "instruction": "Answer ONLY from these passages, briefly. If they do not answer the question, call handover_to_staff."}

    # ------------------------------------------------------------------ payment (app-only)
    def complete_payment(self, success: bool) -> dict[str, Any]:
        """Called by the app, never by the LLM."""
        if not self.payment_pending:
            return {"ok": False, "message": "There is no payment waiting. Pick a slot first."}
        try:
            with SessionLocal() as db:
                pay = payments.settle_payment(db, self.payment_id, success)
                if not success:
                    self._reset_flow()
                    return {"ok": False, "message": "Payment failed, so I released the slot. Say 'book again' to start over."}
                booking = payments.confirm_after_payment(db, self.provider, pay, self.patient.id)
                slot = db.get(Slot, self.current_hold.slot_id)
                doctor_name = DOCTORS.get(slot.doctor_id, {}).get("name", "the doctor") if slot else "the doctor"
                info = confirmation.send_confirmation(
                    db, appointment_id=booking.id, mobile=self.patient.mobile, start_at=booking.start_at,
                    doctor_name=doctor_name, service_name=i18n.service_name(self.service_id, self.language) or SERVICES.get(self.service_id, "Appointment"),
                    amount_paise=pay.amount_paise, language=self.language, session_id=self.session_id,
                )
            self.booking = booking
            self.ics_ref = info["ref"]
            self.ics_link_sent = False
            return {"ok": True, "message": info["message"]}
        except (BookingError, FlowError) as exc:
            self._reset_flow()
            return {"ok": False, "message": str(exc)}
