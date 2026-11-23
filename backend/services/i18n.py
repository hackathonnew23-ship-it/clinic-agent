"""Curated UI strings from i18n/messages.json. Tulu entries are best-effort: any key missing
under "tu" falls back to Kannada, then English. Native-speaker review needed for Hi/Kn/Tu."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "i18n" / "messages.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_FILE.read_text(encoding="utf-8"))


def norm(lang: str | None) -> str:
    lang = (lang or "en").lower()
    return lang if lang in ("en", "hi", "kn", "tu") else "en"


def _chain(lang: str | None) -> list[str]:
    lang = norm(lang)
    return {"tu": ["tu", "kn", "en"], "en": ["en"]}.get(lang, [lang, "en"])


def _lookup(key: str, lang: str | None):
    d = _data()
    for code in _chain(lang):
        if key in d.get(code, {}):
            return d[code][key]
    return None


def t(key: str, lang: str | None = None, **kw) -> str:
    text = _lookup(key, lang)
    if text is None:
        return key
    return text.format(**kw) if kw else text


def day_label(date_str: str, lang: str | None = None) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{_lookup('weekdays', lang)[d.weekday()]} {d.day} {_lookup('months', lang)[d.month - 1]}"


def service_name(service_id: str, lang: str | None = None) -> str | None:
    return (_lookup("services", lang) or {}).get(service_id)
