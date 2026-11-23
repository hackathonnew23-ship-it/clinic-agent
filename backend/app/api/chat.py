"""Chat API for the embeddable widget.
POST /chat        {session_id, message} -> reply + optional slot / payment buttons
GET  /chat/config clinic name + theme for the widget
GET  /widget.js   the one-script-tag widget
GET  /demo        a demo clinic page that embeds it
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from agent.agent import BookingAgent
from agent.tools import BookingTools
from app.db import SessionLocal
from app.models import Clinic
from services.i18n import day_label, t

log = logging.getLogger("chat")
router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent.parent / "static"

SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_SESSIONS = 500
_SESSIONS: "OrderedDict[str, BookingAgent]" = OrderedDict()
_LOCK = threading.Lock()


def _new_agent(sid: str) -> BookingAgent:
    tools = BookingTools(session_id=sid)
    if os.getenv("AGENT_FORCE_SCRIPTED", "0") == "1":
        return BookingAgent(tools=tools, force_scripted=True)
    try:
        return BookingAgent(tools=tools)
    except Exception:  # no Gemini key etc: keep the demo alive
        log.warning("LLM unavailable, using scripted agent", exc_info=True)
        return BookingAgent(tools=tools, force_scripted=True)


def _get_agent(sid: str) -> BookingAgent:
    with _LOCK:
        agent = _SESSIONS.get(sid)
        if agent is None:
            agent = _new_agent(sid)
            agent.lock = threading.Lock()  # one turn at a time per session
            _SESSIONS[sid] = agent
            while len(_SESSIONS) > MAX_SESSIONS:
                _SESSIONS.popitem(last=False)
        else:
            _SESSIONS.move_to_end(sid)
        return agent


class ChatIn(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=1000)
    lang: str | None = Field(default=None, max_length=5)


@router.post("/chat")
def chat(body: ChatIn) -> dict:
    if not SID_RE.match(body.session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    agent = _get_agent(body.session_id)
    with agent.lock:
        tools = agent.tools
        tools.ui_lang = body.lang if body.lang in ("hi", "kn", "tu") else None
        before = tools.last_slots
        try:
            reply = agent.reply(body.message.strip())
        except Exception:
            log.exception("chat turn failed")
            return {"reply": "Sorry, something went wrong. Please try again, or ask the clinic staff for help.",
                    "slots": [], "actions": [], "emergency": False, "handoff": True}
        searched = tools.last_slots is not before  # a new slot search happened this turn
        slots = []
        if searched and tools.current_hold is None:
            slots = [{"label": f"{day_label(s['date'], body.lang)}, {s['time']}", "send": str(i)}
                     for i, s in enumerate(tools.last_slots[:5], 1)]
        actions = []
        if tools.payment_pending:
            actions = [{"label": t("btn_pay", body.lang), "send": "pay"}, {"label": t("btn_fail", body.lang), "send": "fail"}]
        links = []
        ics_ref = getattr(tools, "ics_ref", None)
        if ics_ref and not getattr(tools, "ics_link_sent", True):
            links = [{"href": f"/ics/{ics_ref}"}]
            tools.ics_link_sent = True
        return {
            "reply": reply,
            "links": links,
            "slots": slots,
            "actions": actions,
            "emergency": any(e.startswith("emergency") for e in agent.events),
            "handoff": False,
        }


@router.get("/chat/config")
def chat_config(clinic: str = "clinic_smile") -> dict:
    with SessionLocal() as db:
        row = db.get(Clinic, clinic)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown clinic")
    try:
        theme = json.loads(row.theme_json or "{}")
    except ValueError:
        theme = {}
    return {"name": row.name, "theme": theme if isinstance(theme, dict) else {}, "default_lang": row.default_lang}


@router.get("/widget.js")
def widget_js() -> FileResponse:
    return FileResponse(STATIC / "widget.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/demo", response_class=HTMLResponse)
def demo() -> str:
    return (STATIC / "demo.html").read_text(encoding="utf-8")


@router.get("/ics/{ref}")
def ics_file(ref: str) -> FileResponse:
    if not re.fullmatch(r"[A-Z0-9]{8}", ref):
        raise HTTPException(status_code=404, detail="not found")
    path = STATIC.parent / "outbox" / f"appointment-{ref}.ics"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="text/calendar", filename=f"appointment-{ref}.ics")


@router.get("/site", response_class=HTMLResponse)
def site() -> str:
    return (STATIC / "website.html").read_text(encoding="utf-8")
