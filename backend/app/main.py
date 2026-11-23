"""FastAPI entrypoint.  Run from the backend/ folder:

    uvicorn app.main:app --reload --port 8000

Docs (try the mock clinic API by hand): http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api import clinic_mock
from app.config import get_settings
from app.db import SessionLocal, get_engine, init_engine, is_initialized
from app.models import Base
from services import holds

logger = logging.getLogger("clinic-agent")


def _sweep_once() -> None:
    db = SessionLocal()
    try:
        freed = holds.release_expired(db)
        if freed:
            logger.info("released %d expired slot hold(s)", freed)
    finally:
        db.close()


async def _sweeper_loop(interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await run_in_threadpool(_sweep_once)
        except Exception:  # noqa: BLE001 - never let the sweeper die
            logger.exception("hold sweeper failed")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if not is_initialized():
        init_engine()
    Base.metadata.create_all(get_engine())
    # seed_if_empty: fresh deploys (Render wipes the disk) start with a ready clinic
    try:
        from sqlalchemy import select as _select
        from app.db import SessionLocal as _SL
        from app.models import Clinic as _Clinic
        with _SL() as _db:
            if _db.scalars(_select(_Clinic)).first() is None:
                from seed.seed import seed as _seed
                _seed(_db)
    except Exception:
        logger.exception('startup seed failed')
    task = asyncio.create_task(_sweeper_loop(get_settings().sweeper_interval_seconds))
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Clinic Booking Agent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

clinic_mock.register_error_handlers(app)
app.include_router(clinic_mock.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "booking_provider": get_settings().booking_provider}


from app.api.chat import router as chat_router  # noqa: E402
app.include_router(chat_router)

from app.api.admin import router as admin_router  # noqa: E402
app.include_router(admin_router)
