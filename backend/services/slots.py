"""Rebuild future FREE slots after admin changes to hours/holidays."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Appointment, Slot, SlotStatus
from app.timeutil import utcnow
from seed.seed import _seed_slots


def regenerate_slots(db: Session, days: int = 21) -> int:
    now = utcnow()
    q = delete(Slot).where(Slot.status == SlotStatus.FREE, Slot.start_at >= now)
    col = getattr(Appointment, "slot_id", None)
    if col is not None:
        q = q.where(Slot.id.not_in(select(col).where(col.is_not(None))))
    db.execute(q)
    db.flush()
    created = _seed_slots(db, days)
    db.flush()
    db.execute(delete(Slot).where(Slot.status == SlotStatus.FREE, Slot.start_at < now))
    return created
