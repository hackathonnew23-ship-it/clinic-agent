"""The agent: emergency check first, then payment buttons, then the LLM with tools, then scripted fallback."""
from __future__ import annotations

from datetime import datetime

from agent.emergency import check_emergency
from services.faq import answer_faq
from agent.fallback import scripted_turn
from agent.llm import LLMUnavailable, get_llm, tool_results_message, user_message
from agent.tools import IST, BookingTools, catalog_text, refresh_catalog

MAX_TOOL_ROUNDS = 6
PAY_WORDS = {"pay", "pay now", "paid", "i paid"}
FAIL_WORDS = {"fail", "pay fail", "payment failed"}


def build_system_prompt() -> str:
    now = datetime.now(IST)
    return f"""You are the booking assistant for Smile Dental Clinic in India.
Today is {now.strftime('%A, %Y-%m-%d')} (India time). Use it to turn words like "this Saturday" into dates.

GOAL: book a paid, confirmed appointment in as few messages as possible. From the patient's first message,
extract doctor, service, day and time of day all at once, then call get_available_slots immediately.

DOCTORS, SERVICES AND FEES (use these ids in tools; quote these fees):
{catalog_text()}
If the patient gives no doctor, pick one who offers the service.
If the service is unclear, ask ONE short question.

FLOW (follow in order, never skip a step):
1. Offer at most 5 real slots from get_available_slots as a short numbered list.
2. Patient picks one -> call hold_slot (pass language en/hi/kn/tu). Tell them it is held for 5 minutes,
   show the consent_text, and ask them to reply "I agree" with their 10-digit mobile number.
3. Only when the patient clearly agrees -> record_consent, then send_otp with their number.
   Tell them an OTP was sent. NEVER invent or guess an OTP.
4. Patient types the 6-digit code -> verify_otp. On success tell them the fee and to reply "pay".
   If the code is wrong, tell them how many attempts are left. If locked/expired, offer a new code
   (send_otp again; there is a 30 second wait between codes).
5. Payment is handled by the app when the patient replies "pay". You cannot confirm a booking yourself.
   Never say a booking is confirmed unless the app has told you so.

MANAGING AN EXISTING BOOKING (cancel or reschedule). Use this INSTEAD of the booking flow when the patient
mentions cancel/reschedule/change/move an existing booking. Never ask which service for these.
1. Ask for the booking reference if not given, then call start_manage. It sends an OTP to the mobile on file.
2. Patient types the 6-digit code -> call verify_manage_otp. Show no booking details before it succeeds.
3. Ask whether they want to cancel or reschedule if unclear.
4. CANCEL: call quote_cancel, tell the patient the exact refund_display and refund_percent (never round), ask yes/no.
   Only after a clear yes call cancel_booking with patient_confirmed=true.
5. RESCHEDULE: call list_reschedule_slots, offer up to 5 as a numbered list (same doctor, same fee).
   When the patient picks one and says yes, call reschedule_booking with that slot_id and patient_confirmed=true.
6. If a tool returns an error, explain it simply. Never state a refund or new time the tools did not return.
   If it cannot be fixed, offer to hand over to clinic staff.

CLINIC INFORMATION QUESTIONS (parking, payment methods, insurance, policies, what to bring):
call search_knowledge, then answer ONLY from the passages it returns. If it returns not_found, or the passages
do not answer the question, call handover_to_staff. Never invent clinic facts, prices or policies.
Medical questions are never answered from this: no medical advice.

RULES:
- NEVER SUBSTITUTE a service or doctor. If the patient asks for a service not in the list above, say clearly that the clinic does not offer it, list the services that are offered, and ask which one they want.
- Only offer slots returned by get_available_slots. Never invent times or slot ids.
- NEVER give medical advice, diagnosis, or medicine suggestions. For symptoms or "is this serious?",
  say you can't advise on that and offer to book a consultation with the dentist.
- If unsure, the patient is upset, asks for a human, or asks something outside booking/clinic info that you cannot answer from tools, call handover_to_staff with a short reason, then tell them staff will follow up. Never invent answers.
- Reply in the patient's language (English, Hindi, Kannada or Tulu; Romanized text is fine).
  Keep replies short and friendly. If you cannot write Tulu well, use simple Kannada or English.
"""


class BookingAgent:
    def __init__(self, llm=None, tools: BookingTools | None = None, force_scripted: bool = False) -> None:
        self.tools = tools or BookingTools()
        self.force_scripted = force_scripted
        self.llm = None if force_scripted else (llm or get_llm())
        self.history: list[dict] = []
        self.fallback_state: dict = {}
        self.events: list[str] = []  # debug trail shown by the CLI

    def reply(self, text: str) -> str:
        self.events.clear()
        out = self._reply(text)
        if self.tools.notices:  # test-mode SMS (OTP) shown to the person
            out += "\n" + "\n".join(self.tools.notices)
            self.tools.notices.clear()
        return out

    def _reply(self, text: str) -> str:
        refresh_catalog()
        emergency = check_emergency(text)
        if emergency:
            self.events.append(f"emergency keyword: {emergency.keyword}")
            return emergency.message
        faq_answer = answer_faq(text, getattr(self.tools, 'language', 'en'))
        if faq_answer:
            self.events.append('faq answered')
            return faq_answer

        t = text.strip().lower()
        if self.tools.payment_pending and (t in PAY_WORDS or t in FAIL_WORDS):
            result = self.tools.complete_payment(success=t in PAY_WORDS)
            self.events.append(f"payment {'ok' if result['ok'] else 'not ok'}")
            self.fallback_state.clear()
            return result["message"]

        if self.llm is not None:
            checkpoint = len(self.history)
            try:
                return self._llm_turn(text)
            except LLMUnavailable as exc:
                del self.history[checkpoint:]  # roll back half-finished turn
                self.events.append(f"LLM unavailable ({exc}); using scripted fallback")

        return scripted_turn(text, self.tools, self.fallback_state)

    def _llm_turn(self, text: str) -> str:
        system = build_system_prompt()
        self.history.append(user_message(text))
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self.llm.generate(system, self.history, self.tools.declarations)
            self.history.append(reply.model_content)
            if not reply.tool_calls:
                return reply.text or "Sorry, could you say that again?"
            results = []
            for call in reply.tool_calls:
                out = self.tools.run(call.name, call.args)
                self.events.append(f"tool {call.name}({call.args}) -> {'error' if 'error' in out else 'ok'}")
                results.append((call.name, out))
            self.history.append(tool_results_message(results))
        return "Sorry, I got stuck. Let me connect you with the clinic staff."
