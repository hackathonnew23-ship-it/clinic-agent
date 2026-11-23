"""Scripted booking flow used when Gemini is down, rate-limited or has no key.
Keeps the demo alive. Keyword based. Replies in English, Hindi or Kannada (Tulu falls back
to Kannada) following the widget's language. Stages: offer -> consent -> otp -> pay."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from agent.tools import DOCTORS, IST, SERVICES, BookingTools
from services.i18n import day_label, service_name, t
from agent.cancel_flow import cancel_turn, is_cancel_intent

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SERVICE_WORDS = {
    "svc_cleaning": ["clean", "सफाई", "सफ़ाई", "ಕ್ಲೀನ್"],
    "svc_filling": ["fill", "cavity", "फिलिंग", "फ़िलिंग", "ಫಿಲ್ಲಿಂಗ್"],
    "svc_rootcanal": ["root canal", "rootcanal", "root-canal", "रूट कैनाल", "ರೂಟ್ ಕೆನಾಲ್"],
    "svc_consult": ["consult", "checkup", "check-up", "check up", "परामर्श", "ಸಮಾಲೋಚನೆ"],
}
MOBILE_RE = re.compile(r"(?<!\d)(?:\+?91)?([6-9]\d{9})(?!\d)")
AGREE_RE = re.compile(r"\b(agree|yes|okay|ok|consent)\b|सहमत|हाँ|हां|ಒಪ್ಪ|ಹೌದು")


def _lang(tools: BookingTools) -> str:
    return getattr(tools, "ui_lang", None) or "en"


def _find_service(text: str) -> str | None:
    for sid, words in SERVICE_WORDS.items():
        if any(w in text for w in words):
            return sid
    return None


def _find_doctor(text: str, service_id: str | None) -> str | None:
    if "rao" in text:
        return "doc_rao"
    if "shetty" in text:
        return "doc_shetty"
    if service_id:
        for did, info in DOCTORS.items():
            if service_id in info["services"]:
                return did
    return None


def _find_date(text: str):
    today = datetime.now(IST).date()
    if "tomorrow" in text:
        return today + timedelta(days=1)
    if "today" in text:
        return today
    for i, name in enumerate(WEEKDAYS):
        if name in text:
            return today + timedelta(days=(i - today.weekday()) % 7)
    return today + timedelta(days=1)


def _find_part(text: str) -> str | None:
    for part in ("morning", "afternoon", "evening"):
        if part in text:
            return part
    return None


def _show(slots: list[dict], lang: str) -> str:
    return "\n".join(f"{i}. {day_label(s['date'], lang)}, {s['time']}" for i, s in enumerate(slots[:5], 1))


def _consent_step(t_: str, tools: BookingTools, state: dict) -> str:
    lang = _lang(tools)
    agreed = bool(AGREE_RE.search(t_))
    if agreed and not state.get("consented"):
        out = tools.run("record_consent", {"language": lang})
        if "error" in out:
            return out["message"]
        state["consented"] = True
    if not state.get("consented"):
        return t("consent_needed", lang)
    m = MOBILE_RE.search(re.sub(r"[\s-]", "", t_))
    if not m:
        return t("ask_mobile", lang)
    out = tools.run("send_otp", {"mobile": m.group(1)})
    if "error" in out:
        return out["message"]
    state.update(stage="otp", mobile=m.group(1))
    return t("otp_sent", lang, mobile=out["mobile_masked"])


def _otp_step(t_: str, tools: BookingTools, state: dict) -> str:
    lang = _lang(tools)
    if "resend" in t_:
        out = tools.run("send_otp", {"mobile": state["mobile"]})
        return out["message"] if "error" in out else t("otp_resent", lang)
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", t_)
    if not m:
        return t("otp_ask", lang)
    out = tools.run("verify_otp", {"code": m.group(1)})
    if "error" in out:
        return out["message"]
    state["stage"] = "pay"
    return t("verified", lang, amount=out["amount_rupees"])


def scripted_turn(text: str, tools: BookingTools, state: dict) -> str:
    t_ = text.lower().strip()
    lang = _lang(tools)

    if t_ in ("restart", "book again", "start over"):
        state.clear()
        tools._reset_flow()
        return t("restart", lang)

    if state.get("stage") in ("cancel_ref", "cancel_otp", "cancel_confirm") or is_cancel_intent(t_):
        return cancel_turn(t_, state, tools)
    stage = state.get("stage")
    if stage == "consent":
        return _consent_step(t_, tools, state)
    if stage == "otp":
        return _otp_step(t_, tools, state)
    if stage == "pay":
        return t("pay_hint", lang)

    # Patient picks a number from the offered list
    if re.fullmatch(r"\d", t_) and state.get("offered"):
        idx = int(t_) - 1
        offered = state["offered"]
        if 0 <= idx < len(offered):
            out = tools.run("hold_slot", {"slot_id": offered[idx]["slot_id"], "service_id": state["service_id"]})
            if "error" in out:
                return t("slot_taken", lang, reason=out["message"])
            state.update(stage="consent", offered=[], consented=False)
            when = f"{day_label(offered[idx]['date'], lang)}, {offered[idx]['time']}"
            return (
                f"{t('hold_done', lang, when=when, until=out['held_until'])}\n\n"
                f"{out['consent_text']}\n\n{t('hold_agree', lang)}"
            )
        return t("bad_number", lang)

    service_id = _find_service(t_) or state.get("service_id")
    if not service_id:
        return t("ask_service", lang)
    doctor_id = _find_doctor(t_, service_id) or state.get("doctor_id")
    if not doctor_id:
        return t("ask_doctor", lang)

    explicit = any(w in t_ for w in ("today", "tomorrow", *WEEKDAYS))
    first_day = _find_date(t_)
    for offset in range(1 if explicit else 7):  # no day given: use the soonest day with slots
        day = first_day + timedelta(days=offset)
        out = tools.run(
            "get_available_slots",
            {
                "doctor_id": doctor_id,
                "service_id": service_id,
                "date_from": day.isoformat(),
                "date_to": day.isoformat(),
                "part_of_day": _find_part(t_) or "",
            },
        )
        if "error" in out or out["slots"]:
            break
    if "error" in out:
        return out["message"]
    state.update(service_id=service_id, doctor_id=doctor_id, offered=out["slots"])
    if not out["slots"]:
        return t("no_slots", lang)
    svc = service_name(service_id, lang) or SERVICES[service_id].lower()
    return (
        f"{t('slots_intro', lang, doctor=DOCTORS[doctor_id]['name'], service=svc)}\n"
        f"{_show(out['slots'], lang)}\n{t('pick_number', lang)}"
    )
