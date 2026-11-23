# Clinic Booking Agent

An embeddable AI chat widget that books a **paid, confirmed** dental/medical appointment in 5 messages or fewer. Add one script tag to any site.

## Quick start

```
cd backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --port 8000
```

- Chat demo: http://127.0.0.1:8000/demo
- Admin panel: http://127.0.0.1:8000/admin (password from `ADMIN_PASSWORD` in `.env`, default `admin123`)
- Tests: `python3 -m pytest -q`
- No Gemini key? Run with `AGENT_FORCE_SCRIPTED=1` to use the scripted flow.
- OTP test mode: the code is printed in the server window.

## Embed

Plain HTML (before `</body>`):

```html
<script src="https://YOUR-SERVER/widget.js" data-clinic="clinic_smile" defer></script>
```

React:

```jsx
import { useEffect } from "react";

export default function ClinicWidget() {
  useEffect(() => {
    const s = document.createElement("script");
    s.src = "https://YOUR-SERVER/widget.js";
    s.dataset.clinic = "clinic_smile";
    document.body.appendChild(s);
    return () => { s.remove(); };
  }, []);
  return null;
}
```

WordPress: add the plain HTML tag to the footer with a plugin such as "Insert Headers and Footers", or in your theme's `functions.php`:

```php
add_action('wp_footer', function () {
  echo '<script src="https://YOUR-SERVER/widget.js" data-clinic="clinic_smile" defer></script>';
});
```

The widget renders inside a Shadow DOM, so it cannot clash with the host site's CSS.

## Script attributes

| Attribute | Purpose |
|---|---|
| `data-clinic` | Clinic id on the server (default `clinic_smile`) |
| `data-api` | Server URL; defaults to the origin the script was loaded from |
| `data-name` | Name shown in the widget header |
| `data-color` | Brand colour, e.g. `#0d9488` |
| `data-logo` | URL of the clinic logo |
| `data-lang` | Starting language: `en`, `hi`, `kn`, `tu`; otherwise the clinic default |
| `data-position` | `left` puts the bubble bottom-left; any other value means bottom-right |

Name, colours and default language also come from the server (`/chat/config`), so a clinic can set them once without editing its site.

## How it works

```
Widget (Shadow DOM) -> POST /chat -> Agent
  emergency check -> payment buttons -> LLM with tools -> scripted fallback
Agent tools -> BookingProvider (mock REST clinic | Google Calendar)
```

- The LLM only calls tools; it never writes to the database.
- Slot holds expire automatically and a database constraint prevents double booking.
- Payment states: HELD -> PAYMENT_PENDING -> CONFIRMED | FAILED | EXPIRED. A booking is confirmed only after payment succeeds.
- Cancel needs an OTP sent to the mobile number on the booking. Refunds: more than 24h = 100%, 2 to 24h = 50%, under 2h = none.
- Guardrails: no medical advice, emergency keywords are checked before the LLM, DPDP consent is stored with a timestamp, every booking action is audit-logged.

## Languages

English, Hindi and Kannada are supported. Tulu is best-effort. All non-English strings need native-speaker review before production use.
