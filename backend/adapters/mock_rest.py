"""Adapter (b): talks HTTP to a FHIR-style clinic system.

In the demo that "clinic system" is our own mock (app/api/clinic_mock.py, mounted at
/mock-clinic), but the adapter only knows a base URL, so it could point at any server
that speaks the same small FHIR-style subset.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from adapters.base import (
    BookingDTO,
    CancelResultDTO,
    HoldDTO,
    HoldExpired,
    PatientDTO,
    ProviderError,
    SlotDTO,
    SlotUnavailable,
)
from app.config import get_settings
from app.timeutil import parse_iso

PREFIX = "/mock-clinic/fhir"
_APPT_STATUS = {
    "proposed": "HELD",
    "pending": "PAYMENT_PENDING",
    "booked": "CONFIRMED",
    "cancelled": "CANCELLED",
    "noshow": "EXPIRED",
}


class MockRestAdapter:
    name = "mock_rest"

    def __init__(self, client: httpx.Client | None = None, base_url: str | None = None) -> None:
        # `client` can be any httpx-compatible client; tests pass FastAPI's TestClient.
        self._client = client or httpx.Client(
            base_url=base_url or get_settings().clinic_api_url, timeout=10.0
        )

    # ------------------------------------------------------------------ plumbing
    def _request(self, method: str, path: str, *, slot_id: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, f"{PREFIX}{path}", **kwargs)
        except httpx.TransportError as exc:
            raise ProviderError(f"clinic system unreachable: {exc}") from exc

        if response.status_code == 409:
            raise SlotUnavailable(slot_id or path, "conflict")
        if response.status_code == 410:
            raise HoldExpired(_diagnostics(response))
        if response.status_code >= 400:
            raise ProviderError(f"clinic system returned {response.status_code}: {_diagnostics(response)}")
        return response.json()

    # ------------------------------------------------------------------ BookingProvider
    def get_slots(
        self,
        doctor_id: str,
        service_id: str,
        date_from: date,
        date_to: date,
        part_of_day: str | None = None,
    ) -> list[SlotDTO]:
        params: dict[str, Any] = {
            "schedule": doctor_id,
            "service_type": service_id,
            "start": date_from.isoformat(),
            "end": date_to.isoformat(),
        }
        if part_of_day and part_of_day != "any":
            params["part_of_day"] = part_of_day
        bundle = self._request("GET", "/Slot", params=params)
        slots: list[SlotDTO] = []
        for entry in bundle.get("entry", []):
            res = entry["resource"]
            slots.append(
                SlotDTO(
                    id=res["id"],
                    doctor_id=res["schedule"]["reference"].split("/", 1)[1],
                    start_at=parse_iso(res["start"]),
                    end_at=parse_iso(res["end"]),
                )
            )
        return slots

    def hold_slot(
        self,
        slot_id: str,
        session_id: str,
        ttl_seconds: int = 300,
        service_id: str | None = None,
    ) -> HoldDTO:
        res = self._request(
            "POST",
            f"/Slot/{slot_id}/$hold",
            slot_id=slot_id,
            json={"session_id": session_id, "ttl_seconds": ttl_seconds, "service_id": service_id},
        )
        expires = next(
            (e["valueDateTime"] for e in res.get("extension", []) if e["url"] == "hold-expires"),
            None,
        )
        if expires is None:
            raise ProviderError("clinic system did not return a hold expiry")
        return HoldDTO(
            hold_id=res["id"],
            slot_id=slot_id,
            session_id=session_id,
            expires_at=parse_iso(expires),
            service_id=service_id,
        )

    def find_or_create_patient(self, mobile: str, name: str | None = None) -> PatientDTO:
        res = self._request("POST", "/Patient/$find-or-create", json={"mobile": mobile, "name": name})
        is_new = next((e["valueBoolean"] for e in res.get("extension", []) if e["url"] == "is-new"), False)
        return PatientDTO(
            id=res["id"],
            mobile=res["telecom"][0]["value"],
            name=(res.get("name") or [{}])[0].get("text"),
            is_new=bool(is_new),
        )

    def confirm(self, hold_id: str, patient_id: str, payment_ref: str) -> BookingDTO:
        res = self._request(
            "POST",
            f"/Appointment/{hold_id}/$confirm",
            json={"patient_id": patient_id, "payment_ref": payment_ref},
        )
        return _booking_from_resource(res)

    def reschedule(self, booking_id: str, new_slot_id: str) -> BookingDTO:
        res = self._request("POST", f"/Appointment/{booking_id}/$reschedule", json={"new_slot_id": new_slot_id}, slot_id=new_slot_id)
        return _booking_from_resource(res)

    def cancel(self, booking_id: str, reason: str) -> CancelResultDTO:
        res = self._request("POST", f"/Appointment/{booking_id}/$cancel", json={"reason": reason})
        self.last_cancel = res
        return CancelResultDTO(booking_id=res["booking_id"], status=res["status"])


def _diagnostics(response: httpx.Response) -> str:
    try:
        return response.json()["issue"][0]["diagnostics"]
    except Exception:  # noqa: BLE001 - best effort error text
        return response.text[:200]


def _booking_from_resource(res: dict[str, Any]) -> BookingDTO:
    patient_ref = next(
        (p["actor"]["reference"] for p in res.get("participant", []) if "Patient/" in p["actor"]["reference"]),
        None,
    )
    return BookingDTO(
        id=res["id"],
        slot_id=res["slot"][0]["reference"].split("/", 1)[1],
        patient_id=patient_ref.split("/", 1)[1] if patient_ref else None,
        status=_APPT_STATUS.get(res["status"], res["status"].upper()),
        start_at=parse_iso(res["start"]),
        ics_uid=next((e["valueString"] for e in res.get("extension", []) if e["url"] == "ics-uid"), None),
        external_ref=next(
            (i["value"] for i in res.get("identifier", []) if i.get("system") == "clinic-ref"), None
        ),
    )
