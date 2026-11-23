"""Emergency keyword check. Runs BEFORE the LLM, so it never depends on AI.

Covers English, Hindi, Kannada and Tulu, in native script and Romanized spellings.
NOTE: the Tulu message is a placeholder (Kannada + English). A native Tulu speaker
must review it. Keyword lists are deliberately broad: a false alarm is safe,
a miss is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS: dict[str, list[str]] = {
    "en": [
        "chest pain", "heavy bleeding", "bleeding a lot", "bleeding heavily",
        "won't stop bleeding", "cant stop bleeding", "can't breathe", "cannot breathe",
        "difficulty breathing", "trouble breathing", "shortness of breath",
        "unconscious", "fainted", "heart attack", "stroke", "seizure", "overdose",
        "suicidal", "want to die", "severe allergic", "swollen throat",
    ],
    "hi": [
        "सीने में दर्द", "छाती में दर्द", "सीने मे दर्द", "बहुत खून", "खून बंद नहीं",
        "खून रुक नहीं", "सांस नहीं", "सांस लेने में तकलीफ", "साँस लेने में तकलीफ",
        "बेहोश", "दिल का दौरा", "दौरा पड़", "ज़हर",
        "seene mein dard", "seene me dard", "chhati mein dard", "chaati mein dard",
        "bahut khoon", "khoon band nahi", "khoon nahi ruk", "saans nahi",
        "saans lene mein takleef", "behosh", "dil ka daura",
    ],
    "kn": [
        "ಎದೆ ನೋವು", "ಎದೆನೋವು", "ತೀವ್ರ ರಕ್ತಸ್ರಾವ", "ರಕ್ತ ನಿಲ್ಲುತ್ತಿಲ್ಲ", "ಜಾಸ್ತಿ ರಕ್ತ",
        "ಉಸಿರಾಟದ ತೊಂದರೆ", "ಉಸಿರಾಡಲು ಆಗುತ್ತಿಲ್ಲ", "ಪ್ರಜ್ಞೆ ತಪ್ಪ", "ಹೃದಯಾಘಾತ",
        "ede novu", "edenovu", "raktasrava", "rakta nillutilla", "jaasti rakta",
        "usiraatada tondare", "usiraadalu aaguttilla", "prajne tappi", "hrudayaghata",
    ],
    "tcy": [  # Tulu: REVIEW WITH A NATIVE SPEAKER
        "ಎದೆ ನೋವು", "ede novu", "ede novuda", "ratta nillandu", "ratta jasti",
        "usiru tegonuna", "usiru tondare",
    ],
}

EXTRA_KEYWORDS = {
    "en": ["not breathing", "stopped breathing", "knocked out", "poisoned", "swallowed poison", "kill myself",
           "end my life", "blood won't stop", "bleeding won't stop", "face is swelling", "tongue is swelling"],
    "hi": ["दम घुट", "सांस फूल", "खून बह रहा", "बहुत ज्यादा खून", "आत्महत्या", "मरना चाहता", "मरना चाहती",
           "dum ghut", "saans phool", "khoon beh raha", "bahut zyada khoon", "aatmahatya", "marna chahta",
           "marna chahti", "behoshi"],
    "kn": ["ಸಾಯಬೇಕು", "ಆತ್ಮಹತ್ಯೆ", "ರಕ್ತ ಸುರಿ", "ಉಸಿರು ಕಟ್ಟ", "ಮೂರ್ಛೆ",
           "saayabeku", "aatmahatye", "rakta suri", "usiru kattu", "murche", "usiru nillutide"],
}
for _lang, _words in EXTRA_KEYWORDS.items():
    KEYWORDS[_lang] = KEYWORDS[_lang] + _words


MESSAGES: dict[str, str] = {
    "en": (
        "This could be an emergency. Please call the clinic right now, or call "
        "emergency services (112 or 108) immediately. I can't help with medical "
        "emergencies, so please do not wait for a booking."
    ),
    "hi": (
        "यह आपातकाल हो सकता है। कृपया तुरंत क्लिनिक को कॉल करें या आपातकालीन सेवा "
        "(112 या 108) पर कॉल करें। कृपया बुकिंग का इंतज़ार न करें।"
    ),
    "kn": (
        "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಆಗಿರಬಹುದು. ದಯವಿಟ್ಟು ತಕ್ಷಣ ಕ್ಲಿನಿಕ್‌ಗೆ ಕರೆ ಮಾಡಿ ಅಥವಾ "
        "ತುರ್ತು ಸೇವೆ (112 ಅಥವಾ 108) ಗೆ ಕರೆ ಮಾಡಿ. ಬುಕಿಂಗ್‌ಗಾಗಿ ಕಾಯಬೇಡಿ."
    ),
    # TODO(native speaker): replace with proper Tulu. Kannada + English for now.
    "tcy": (
        "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಆಗಿರಬಹುದು. ದಯವಿಟ್ಟು ತಕ್ಷಣ ಕ್ಲಿನಿಕ್‌ಗೆ ಕರೆ ಮಾಡಿ ಅಥವಾ 112 / 108 ಗೆ ಕರೆ ಮಾಡಿ.\n"
        "This could be an emergency. Call the clinic or 112 / 108 now."
    ),
}


@dataclass(frozen=True)
class EmergencyResult:
    language: str
    keyword: str
    message: str


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _script_language(text: str) -> str | None:
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    if re.search(r"[\u0C80-\u0CFF]", text):
        return "kn"
    return None


def check_emergency(text: str) -> EmergencyResult | None:
    """Return an EmergencyResult if the text contains an emergency phrase, else None."""
    norm = _normalize(text)
    script_lang = _script_language(norm)
    for lang, words in KEYWORDS.items():
        for word in words:
            if word in norm:
                reply_lang = script_lang or ("en" if lang == "en" else lang)
                if lang == "tcy" and script_lang is None:
                    reply_lang = "en"  # Romanized Tulu: safest to answer in English
                return EmergencyResult(reply_lang, word, MESSAGES[reply_lang])
    return None