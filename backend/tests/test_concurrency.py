"""THE RACE TEST: 10 parallel requests at one slot -> exactly 1 wins.

Run it on its own (this is the live-demo moment):

    make race          # or:  cd backend && python -m pytest tests/test_concurrency.py -v -s

It is exercised at three levels: the service layer, the raw HTTP API and the adapter.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from adapters.mock_rest import MockRestAdapter
from app.db import SessionLocal
from app.main import app
from app.models import Appointment, AppointmentStatus, Slot, SlotStatus
from services import holds
from services.errors import SlotUnavailable

N = 10


def _race(attempt: Callable[[int], tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Release N threads at the same instant and collect (outcome, detail) from each."""
    barrier = threading.Barrier(N)

    def runner(i: int) -> tuple[str, str | None]:
        barrier.wait(timeout=10)
        return attempt(i)

    with ThreadPoolExecutor(max_workers=N) as pool:
        return list(pool.map(runner, range(N)))


def _assert_exactly_one_winner(results: list[tuple[str, str | None]], slot_id: str) -> None:
    wins = [r for r in results if r[0] == "won"]
    losses = [r for r in results if r[0] == "lost"]
    print(f"\n  -> {len(wins)} won, {len(losses)} lost (of {N} parallel requests)")
    assert len(results) == N
    assert len(wins) == 1, f"expected exactly 1 winner, got {len(wins)}: {results}"
    assert len(losses) == N - 1

    # The database must agree: slot held by the winner, exactly one active appointment.
    check = SessionLocal()
    try:
        slot = check.get(Slot, slot_id)
        assert slot is not None and slot.status == SlotStatus.HELD
        active = check.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.slot_id == slot_id,
                Appointment.status.in_(AppointmentStatus.ACTIVE),
            )
        )
        assert active == 1
    finally:
        check.close()


def test_service_layer_ten_parallel_holds(db_env, slot_id):
    def attempt(i: int) -> tuple[str, str | None]:
        session = SessionLocal()  # each thread gets its own DB session/connection
        try:
            appt = holds.hold_slot(session, slot_id, f"sess_{i}", service_id="svc_cleaning")
            return ("won", appt.session_id)
        except SlotUnavailable:
            return ("lost", None)
        finally:
            session.close()

    results = _race(attempt)
    _assert_exactly_one_winner(results, slot_id)

    # The slot is held by exactly the session that won.
    winner_session = next(r[1] for r in results if r[0] == "won")
    check = SessionLocal()
    try:
        assert check.get(Slot, slot_id).held_by == winner_session
    finally:
        check.close()


def test_http_api_ten_parallel_holds(db_env, slot_id):
    def attempt(i: int) -> tuple[str, str | None]:
        client = TestClient(app)
        resp = client.post(
            f"/mock-clinic/fhir/Slot/{slot_id}/$hold",
            json={"session_id": f"sess_{i}", "service_id": "svc_cleaning"},
        )
        if resp.status_code == 201:
            return ("won", resp.json()["id"])
        assert resp.status_code == 409, resp.text
        return ("lost", None)

    _assert_exactly_one_winner(_race(attempt), slot_id)


def test_adapter_ten_parallel_holds(db_env, slot_id):
    def attempt(i: int) -> tuple[str, str | None]:
        adapter = MockRestAdapter(client=TestClient(app))
        try:
            hold = adapter.hold_slot(slot_id, f"sess_{i}", service_id="svc_cleaning")
            return ("won", hold.hold_id)
        except SlotUnavailable:
            return ("lost", None)

    _assert_exactly_one_winner(_race(attempt), slot_id)
