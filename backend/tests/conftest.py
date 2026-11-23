"""Shared fixtures: every test gets a fresh seeded SQLite FILE database.

A real file (not :memory:) is required so parallel threads each get their own
connection, which is what makes the concurrency tests meaningful.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal, get_engine, init_engine, reset_engine
from app.models import Base, Slot, SlotStatus
from app.timeutil import utcnow
from seed.seed import seed


@pytest.fixture()
def db_env(tmp_path, monkeypatch) -> Iterator[str]:
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    init_engine(url)
    Base.metadata.create_all(get_engine())
    session = SessionLocal()
    seed(session, days=21)
    session.close()
    yield url
    reset_engine()


@pytest.fixture()
def db(db_env):
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def slot_id(db) -> str:
    """A FREE Dr. Rao slot at least two days ahead (so 'now' shifts in tests are safe)."""
    found = db.scalar(
        select(Slot.id)
        .where(
            Slot.doctor_id == "doc_rao",
            Slot.status == SlotStatus.FREE,
            Slot.start_at > utcnow() + timedelta(days=2),
        )
        .order_by(Slot.start_at)
        .limit(1)
    )
    assert found is not None, "seed produced no future slots"
    return found
