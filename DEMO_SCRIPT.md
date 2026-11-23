# 3-minute demo

Setup: server running, /demo and /admin open in two tabs, server window visible (the OTP prints there).

1. **Hook (15s).** "A product, not a bot. One script tag, and any clinic site books paid appointments."
2. **Book (60s).** Open the bubble. Type `book a cleaning with Dr. Rao tomorrow`.
   - Message 2: slot number `1`
   - Message 3: `yes` (DPDP consent, stored with a timestamp)
   - Message 4: mobile number
   - Message 5: the OTP from the server window
   - Then click **Pay**. Confirmation and **Add to calendar** appear. Five typed messages plus one button.
3. **Admin (30s).** In /admin > Services, change the cleaning fee and save. Book again to show the new fee. Add a holiday and show that day has no slots.
4. **FAQ and safety (30s).** Type `what are your timings` (answers from the admin FAQs). Type `I have severe chest pain` (emergency message, the LLM is never called).
5. **Languages (15s).** Switch the dropdown to HI or KN and start a booking.
6. **Cancel (30s).** `cancel my appointment`, the booking ref, the OTP, then the refund amount (100% / 50% / 0% by time left), then `yes`.
7. **Close (10s).** "Holds expire, double booking is blocked by the database, payment confirms the booking, every action is audit-logged."

Before going on stage: save any holiday in /admin once (rebuilds slots), and book one appointment as a rehearsal.
