"""Mobile OTP in TEST MODE: 6 digits, 5 min expiry, 3 attempts, 30 s resend cooldown.
The code is delivered through the mock SMS outbox, never returned to the LLM."""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OtpSession, new_id
from app.timeutil import utcnow
from services import audit
from services.confirmation import send_sms
from services.flow_errors import FlowError

OTP_TTL_SECONDS = 300
MAX_ATTEMPTS = 3
RESEND_COOLDOWN_SECONDS = 30
_SECRET = os.getenv("OTP_SECRET", "dev-only-secret")


def normalize_mobile(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise FlowError("bad_mobile", "That mobile number doesn't look right. Please send a 10-digit Indian mobile number.")
    return "+91" + digits


def _hash(session_id: str, code: str) -> str:
    return hmac.new(_SECRET.encode(), f"{session_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _latest(db: Session, session_id: str) -> OtpSession | None:
    return db.scalar(select(OtpSession).where(OtpSession.session_id == session_id).order_by(OtpSession.last_sent_at.desc()))


def send_otp(db: Session, session_id: str, mobile_raw: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    mobile = normalize_mobile(mobile_raw)
    last = _latest(db, session_id)
    if last and last.verified_at is None:
        elapsed = (now - last.last_sent_at).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            wait = math.ceil(RESEND_COOLDOWN_SECONDS - elapsed)
            raise FlowError("otp_cooldown", f"Please wait {wait} seconds before requesting a new code.", wait_seconds=wait)
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(OtpSession(
        id=new_id("otp"), session_id=session_id, mobile=mobile, code_hash=_hash(session_id, code),
        attempts=0, last_sent_at=now, expires_at=now + timedelta(seconds=OTP_TTL_SECONDS),
    ))
    audit.log(db, actor="system", action="otp_sent", entity="otp", session_id=session_id,
              payload={"mobile_last4": mobile[-4:]}, now=now)
    db.commit()
    send_sms(mobile, f"Your Smile Dental verification code is {code}. Valid for 5 minutes.")
    return {"mobile": mobile, "masked": "+91******" + mobile[-4:], "test_code": code}


def verify_otp(db: Session, session_id: str, code: str, now: datetime | None = None) -> str:
    """Returns the verified normalized mobile, or raises FlowError."""
    now = now or utcnow()
    row = _latest(db, session_id)
    if row is None:
        raise FlowError("no_otp", "No code was requested yet. Please share your mobile number first.")
    if row.verified_at is not None:
        return row.mobile
    if now > row.expires_at:
        raise FlowError("otp_expired", "That code has expired. Please ask for a new one.")
    if row.attempts >= MAX_ATTEMPTS:
        raise FlowError("otp_locked", "Too many wrong attempts. Please ask for a new code.")
    if not hmac.compare_digest(row.code_hash, _hash(session_id, (code or "").strip())):
        row.attempts += 1
        left = MAX_ATTEMPTS - row.attempts
        audit.log(db, actor="patient", action="otp_failed", entity="otp", entity_id=row.id,
                  session_id=session_id, payload={"attempts": row.attempts}, now=now)
        db.commit()
        if left <= 0:
            raise FlowError("otp_locked", "Too many wrong attempts. Please ask for a new code.")
        raise FlowError("otp_wrong", f"That code is wrong. {left} attempt(s) left.", attempts_left=left)
    row.verified_at = now
    audit.log(db, actor="patient", action="otp_verified", entity="otp", entity_id=row.id,
              session_id=session_id, payload={"mobile_last4": row.mobile[-4:]}, now=now)
    db.commit()
    return row.mobile
