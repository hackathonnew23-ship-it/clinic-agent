"""Answer from the admin FAQs table by word overlap. No LLM, no medical advice."""
import re

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Faq

CLINIC_ID = "clinic_smile"
STOP = {"the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "my", "me", "can", "to",
        "of", "in", "on", "at", "for", "and", "or", "what", "how", "when", "where", "which", "it", "we"}


BOOKING = {"book", "booking", "appointment", "cleaning", "filling", "root", "canal", "consultation", "slot", "tomorrow", "today", "reschedule"}


def _tok(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", s.lower()) if w not in STOP and len(w) > 1}


def answer_faq(text: str, lang: str = "en") -> str | None:
    q = _tok(text)
    if not q:
        return None
    langs = [lang, "en"] + (["kn"] if lang == "tu" else [])
    with SessionLocal() as db:
        rows = db.scalars(select(Faq).where(Faq.clinic_id == CLINIC_ID, Faq.lang.in_(langs))).all()
        best, best_score = None, 0.0
        for r in rows:
            f = _tok(r.question)
            if not f:
                continue
            ov = len(q & f)
            ok = ov >= 1 and ov / len(f) >= 0.5 and ov / len(q) >= 0.5 and not (q & BOOKING)
            score = ov / len(f)
            if ok and r.lang == lang:
                score += 0.5
            if ok and score > best_score:
                best, best_score = r.answer, score
        return best
