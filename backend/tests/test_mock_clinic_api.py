"""The FHIR-style mock clinic API and the adapter that talks to it."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from adapters.base import BookingProvider, HoldExpired, SlotUnavailable
from adapters.mock_rest import MockRestAdapter
from app.main import app
from app.timeutil import parse_iso

TZ = ZoneInfo("Asia/Kolkata")


@pytest.fixture()
def client(db_env) -> TestClient:
    return TestClient(app)


def _next_saturday() -> datetime:
    today = datetime.now(TZ).date()
    days_ahead = (5 - today.weekday()) % 7 or 7
    return datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_saturday_morning_slots_for_dr_rao(client):
    sat = _next_saturday().date().isoformat()
    resp = client.get(
        "/mock-clinic/fhir/Slot",
        params={"schedule": "doc_rao", "start": sat, "end": sat, "part_of_day": "morning"},
    )
    assert resp.status_code == 200
    bundle = resp.json()
    assert bundle["resourceType"] == "Bundle" and bundle["total"] == 6  # 09:00-13:00 in 30-min slots
    for entry in bundle["entry"]:
        local = parse_iso(entry["resource"]["start"]).astimezone(TZ)
        assert local.date().isoformat() == sat and 9 <= local.hour < 12


def test_hold_then_conflict_then_confirm_over_http(client):
    sat = _next_saturday().date().isoformat()
    slots = client.get(
        "/mock-clinic/fhir/Slot", params={"schedule": "doc_rao", "start": sat, "end": sat}
    ).json()["entry"]
    slot_id = slots[0]["resource"]["id"]

    hold = client.post(f"/mock-clinic/fhir/Slot/{slot_id}/$hold", json={"session_id": "s1"})
    assert hold.status_code == 201
    appt = hold.json()
    assert appt["status"] == "proposed"
    assert any(e["url"] == "hold-expires" for e in appt["extension"])

    conflict = client.post(f"/mock-clinic/fhir/Slot/{slot_id}/$hold", json={"session_id": "s2"})
    assert conflict.status_code == 409
    assert conflict.json()["resourceType"] == "OperationOutcome"

    patient = client.post(
        "/mock-clinic/fhir/Patient/$find-or-create", json={"mobile": "9845012345", "name": "Asha"}
    ).json()
    assert patient["extension"][0]["valueBoolean"] is True

    confirmed = client.post(
        f"/mock-clinic/fhir/Appointment/{appt['id']}/$confirm",
        json={"patient_id": patient["id"], "payment_ref": "pay_mock_1"},
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "booked"

    still_free = client.get(
        "/mock-clinic/fhir/Slot", params={"schedule": "doc_rao", "start": sat, "end": sat}
    ).json()["entry"]
    assert slot_id not in {e["resource"]["id"] for e in still_free}


def test_invalid_mobile_returns_422(client):
    resp = client.post("/mock-clinic/fhir/Patient/$find-or-create", json={"mobile": "1234567890"})
    assert resp.status_code == 422


def test_unknown_appointment_is_404(client):
    assert client.get("/mock-clinic/fhir/Appointment/appt_nope").status_code == 404


def test_adapter_full_flow(db_env):
    adapter = MockRestAdapter(client=TestClient(app))
    assert isinstance(adapter, BookingProvider)

    sat = _next_saturday().date()
    slots = adapter.get_slots("doc_rao", "svc_cleaning", sat, sat, "morning")
    assert len(slots) == 6

    hold = adapter.hold_slot(slots[0].id, "sess_a", service_id="svc_cleaning")
    assert hold.expires_at > datetime.now(TZ)
    with pytest.raises(SlotUnavailable):
        adapter.hold_slot(slots[0].id, "sess_b")

    patient = adapter.find_or_create_patient("9845012345", "Asha")
    assert patient.is_new and patient.mobile == "+919845012345"
    assert not adapter.find_or_create_patient("9845012345").is_new

    booking = adapter.confirm(hold.hold_id, patient.id, "pay_mock_1")
    assert booking.status == "CONFIRMED" and booking.patient_id == patient.id and booking.ics_uid

    # An already-confirmed booking cannot be confirmed for a different patient.
    intruder = adapter.find_or_create_patient("9845099999", "Ravi")
    with pytest.raises(HoldExpired):
        adapter.confirm(hold.hold_id, intruder.id, "pay_mock_2")
