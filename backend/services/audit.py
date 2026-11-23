"""Append-only audit log. Every booking action goes through here."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.timeutil import utcnow


def log(
    db: Session,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: str | None = None,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Add an audit row to the CURRENT transaction (the caller commits).

    Writing it in the same transaction as the change means the log can never
    disagree with the data: both commit or both roll back.
    """
    db.add(
        AuditLog(
            ts=now or utcnow(),
            session_id=session_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            payload_json=json.dumps(payload or {}, default=str, ensure_ascii=False),
        )
    )
