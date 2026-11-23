"""DPDP consent: explicit, versioned, timestamped, language recorded."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Consent, new_id
from app.timeutil import utcnow
from services import audit

CONSENT_VERSION = "dpdp-v1"

# Hindi / Kannada texts need native-speaker review in Phase 6. Tulu falls back to Kannada.
CONSENT_TEXT = {
    "en": (
        "To book, Smile Dental Clinic will collect and use your name, mobile number and "
        "appointment details only for this booking, reminders and billing. You can withdraw "
        "consent anytime by contacting the clinic."
    ),
    "hi": (
        "बुकिंग के लिए Smile Dental Clinic आपका नाम, मोबाइल नंबर और अपॉइंटमेंट की जानकारी केवल इस बुकिंग, "
        "रिमाइंडर और बिलिंग के लिए इकट्ठा और उपयोग करेगा। आप क्लिनिक से संपर्क करके कभी भी सहमति वापस ले सकते हैं।"
    ),
    "kn": (
        "ಬುಕಿಂಗ್‌ಗಾಗಿ Smile Dental Clinic ನಿಮ್ಮ ಹೆಸರು, ಮೊಬೈಲ್ ಸಂಖ್ಯೆ ಮತ್ತು ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ವಿವರಗಳನ್ನು ಈ ಬುಕಿಂಗ್, "
        "ಜ್ಞಾಪನೆ ಮತ್ತು ಬಿಲ್ಲಿಂಗ್‌ಗಾಗಿ ಮಾತ್ರ ಸಂಗ್ರಹಿಸಿ ಬಳಸುತ್ತದೆ. ನೀವು ಯಾವಾಗ ಬೇಕಾದರೂ ಕ್ಲಿನಿಕ್ ಅನ್ನು ಸಂಪರ್ಕಿಸಿ ಸಮ್ಮತಿಯನ್ನು ಹಿಂಪಡೆಯಬಹುದು."
    ),
}


def norm_lang(language: str | None) -> str:
    lang = (language or "en").lower()
    if lang == "tu":
        return "kn"
    return lang if lang in CONSENT_TEXT else "en"


def get_consent_text(language: str | None) -> str:
    return CONSENT_TEXT[norm_lang(language)]


def record_consent(db: Session, session_id: str, language: str | None) -> Consent:
    """Idempotent per session: a second call returns the first consent row."""
    existing = db.scalar(select(Consent).where(Consent.session_id == session_id))
    if existing:
        return existing
    lang = norm_lang(language)
    row = Consent(id=new_id("cons"), session_id=session_id, text_version=CONSENT_VERSION, language=lang, granted_at=utcnow())
    db.add(row)
    audit.log(
        db, actor="patient", action="consent_granted", entity="consent", entity_id=row.id,
        session_id=session_id, payload={"version": CONSENT_VERSION, "language": lang},
    )
    db.commit()
    return row
