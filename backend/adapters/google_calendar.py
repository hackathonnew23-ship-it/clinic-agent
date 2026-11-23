"""Adapter (a): Google Calendar.

Our database stays the source of truth for holds and slot locking, so double booking
is impossible exactly as with the mock clinic. This adapter wraps that inner provider and:
  * hides slots that overlap busy time in the clinic's Google Calendar (staff can block time there)
  * creates a calendar event when a booking is confirmed, moves it on reschedule, deletes it on cancel
Real mode needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN (and optionally
GOOGLE_CALENDAR_ID, default "primary"). Without them, or with GOOGLE_CALENDAR_MOCK=1, events are
appended to outbox/google_calendar_mock.jsonl instead. A Google outage never breaks a paid booking.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from adapters.base import BookingDTO, CancelResultDTO, HoldDTO, PatientDTO, SlotDTO
from adapters.mock_rest import MockRestAdapter
from app.config import get_settings

log = logging.getLogger("gcal")
API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
OUTBOX = Path(__file__).resolve().parent.parent / "outbox"
TZ = ZoneInfo("Asia/Kolkata")
DEFAULT_MINUTES = 30


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class GoogleCalendarAdapter:
    name = "google_calendar"

    def __init__(self, inner=None, http: httpx.Client | None = None) -> None:
        get_settings()  # makes sure .env is loaded
        self._inner = inner or MockRestAdapter()
        self._http = http or httpx.Client(timeout=10.0)
        self._cal = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        creds = [os.getenv(k, "") for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")]
        self._creds = creds
        self.mock = os.getenv("GOOGLE_CALENDAR_MOCK", "0") == "1" or not all(creds)
        self._token = ""
        self._token_exp = 0.0
        self._slots: dict[str, SlotDTO] = {}

    # ------------------------------------------------------------------ google plumbing
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        cid, secret, refresh = self._creds
        r = self._http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                             "refresh_token": refresh, "grant_type": "refresh_token"})
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, ok: tuple[int, ...] = (), **kwargs):
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        r = self._http.request(method, f"{API}{path}", headers=headers, **kwargs)
        if r.status_code >= 400 and r.status_code not in ok:
            r.raise_for_status()
        return r.json() if r.content else {}

    def _busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        if self.mock:
            return []
        body = {"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": self._cal}]}
        data = self._call("POST", "/freeBusy", json=body)
        busy = data.get("calendars", {}).get(self._cal, {}).get("busy", [])
        return [(_parse(b["start"]), _parse(b["end"])) for b in busy]

    # ------------------------------------------------------------------ event sync
    @staticmethod
    def _event_id(booking_id: str) -> str:
        return "bk" + hashlib.sha1(booking_id.encode()).hexdigest()[:24]  # hex is valid for Google ids

    def _body(self, b: BookingDTO) -> dict:
        slot = self._slots.get(b.slot_id)
        end = slot.end_at if slot and slot.start_at == b.start_at else b.start_at + timedelta(minutes=DEFAULT_MINUTES)
        return {
            "summary": "Dental appointment",
            "description": f"Booking ref {b.id}. Booked via the clinic assistant.",
            "start": {"dateTime": _iso(b.start_at)},
            "end": {"dateTime": _iso(end)},
        }

    def _sync(self, action: str, booking_id: str, body: dict | None = None) -> str | None:
        eid = self._event_id(booking_id)
        try:
            if self.mock:
                OUTBOX.mkdir(exist_ok=True)
                rec = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, "event_id": eid, "event": body}
                with (OUTBOX / "google_calendar_mock.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                return "mock-" + eid
            if action == "create":
                self._call("POST", f"/calendars/{self._cal}/events", ok=(409,), json={"id": eid, **(body or {})})
            elif action == "update":
                self._call("PATCH", f"/calendars/{self._cal}/events/{eid}", json=body or {})
            elif action == "delete":
                self._call("DELETE", f"/calendars/{self._cal}/events/{eid}", ok=(404, 410))
            return eid
        except Exception:  # noqa: BLE001 - a calendar outage must not undo a paid booking
            log.exception("google calendar %s failed for booking %s", action, booking_id)
            return None

    # ------------------------------------------------------------------ BookingProvider
    def get_slots(self, doctor_id: str, service_id: str, date_from: date, date_to: date,
                  part_of_day: str | None = None) -> list[SlotDTO]:
        slots = self._inner.get_slots(doctor_id, service_id, date_from, date_to, part_of_day)
        for s in slots:
            self._slots[s.id] = s
        if not slots:
            return slots
        try:
            window_start = datetime.combine(date_from, dtime.min, tzinfo=TZ)
            window_end = datetime.combine(date_to + timedelta(days=1), dtime.min, tzinfo=TZ)
            busy = self._busy(window_start, window_end)
        except Exception:  # noqa: BLE001 - if Google is down, fall back to our own availability
            log.exception("google freebusy failed; showing unfiltered slots")
            return slots
        return [s for s in slots if not any(b0 < s.end_at and s.start_at < b1 for b0, b1 in busy)]

    def hold_slot(self, slot_id: str, session_id: str, ttl_seconds: int = 300,
                  service_id: str | None = None) -> HoldDTO:
        return self._inner.hold_slot(slot_id, session_id, ttl_seconds, service_id)

    def find_or_create_patient(self, mobile: str, name: str | None = None) -> PatientDTO:
        return self._inner.find_or_create_patient(mobile, name)

    def confirm(self, hold_id: str, patient_id: str, payment_ref: str) -> BookingDTO:
        b = self._inner.confirm(hold_id, patient_id, payment_ref)
        ref = self._sync("create", b.id, self._body(b))
        return replace(b, external_ref=ref or b.external_ref)

    def reschedule(self, booking_id: str, new_slot_id: str) -> BookingDTO:
        b = self._inner.reschedule(booking_id, new_slot_id)
        ref = self._sync("update", b.id, self._body(b))
        return replace(b, external_ref=ref or b.external_ref)

    def cancel(self, booking_id: str, reason: str) -> CancelResultDTO:
        result = self._inner.cancel(booking_id, reason)
        self._sync("delete", booking_id)
        return result
