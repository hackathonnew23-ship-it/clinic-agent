/*! Clinic booking widget: one script tag, Shadow DOM, no build step. */
(() => {
  const script = document.currentScript;
  if (!script || window.__clinicWidgetLoaded) return;
  window.__clinicWidgetLoaded = true;

  const ds = script.dataset;
  const API = (ds.api || new URL(script.src, location.href).origin).replace(/\/$/, "");
  const CLINIC = ds.clinic || "clinic_smile";
  const SID = (crypto.randomUUID ? crypto.randomUUID() : Date.now() + Math.random().toString(36).slice(2)).replace(/-/g, "");

  // Hindi / Kannada strings need native-speaker review (Phase 6).
  const STR = {
    en: { title: "Book an appointment", greet: "Hi! I can book your appointment in a few messages. What do you need, and when?",
          ph: "Type your message…", send: "Send", note: "AI assistant. I can't give medical advice.", err: "Sorry, I couldn't reach the clinic. Please try again.", wait: "Typing…" },
    hi: { title: "अपॉइंटमेंट बुक करें", greet: "नमस्ते! मैं कुछ ही संदेशों में आपकी अपॉइंटमेंट बुक कर सकता हूँ। आपको क्या चाहिए और कब?",
          ph: "अपना संदेश लिखें…", send: "भेजें", note: "AI सहायक। मैं चिकित्सा सलाह नहीं दे सकता।", err: "क्षमा करें, क्लिनिक से संपर्क नहीं हो पाया। कृपया फिर कोशिश करें।", wait: "लिख रहा है…" },
    kn: { title: "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಿ", greet: "ನಮಸ್ಕಾರ! ಕೆಲವೇ ಸಂದೇಶಗಳಲ್ಲಿ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡುತ್ತೇನೆ. ನಿಮಗೆ ಏನು ಬೇಕು, ಯಾವಾಗ?",
          ph: "ನಿಮ್ಮ ಸಂದೇಶ ಬರೆಯಿರಿ…", send: "ಕಳುಹಿಸಿ", note: "AI ಸಹಾಯಕ. ನಾನು ವೈದ್ಯಕೀಯ ಸಲಹೆ ನೀಡಲಾರೆ.", err: "ಕ್ಷಮಿಸಿ, ಕ್ಲಿನಿಕ್ ಸಂಪರ್ಕಿಸಲಾಗಲಿಲ್ಲ. ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.", wait: "ಬರೆಯುತ್ತಿದೆ…" },
  };
  // Tulu is best-effort and needs native-speaker review; unlisted strings fall back to Kannada.
  STR.tu = Object.assign({}, STR.kn, {
    title: "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಲ್ಪುಲೆ",
    greet: "ನಮಸ್ಕಾರ! ಕೆಲವು ಸಂದೇಶೊಡು ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಲ್ಪುವೆ. ಇರೆಗ್ ಏನ್ ಬೋಡು, ಏಪ?",
    ph: "ಇರೆನ ಸಂದೇಶ ಬರೆಲೆ…",
  });
  const CHIPS = ["teeth cleaning", "consultation", "root canal"];  // sent to the agent as keywords
  STR.en.chips = ["Teeth cleaning", "Consultation", "Root canal"];
  STR.hi.chips = ["दाँतों की सफ़ाई", "परामर्श", "रूट कैनाल"];
  STR.kn.chips = ["ಹಲ್ಲು ಕ್ಲೀನಿಂಗ್", "ಸಮಾಲೋಚನೆ", "ರೂಟ್ ಕೆನಾಲ್"];
  STR.tu.chips = STR.kn.chips;
  STR.en.ics = "Add to calendar"; STR.hi.ics = "कैलेंडर में जोड़ें"; STR.kn.ics = "ಕ್ಯಾಲೆಂಡರ್‌ಗೆ ಸೇರಿಸಿ"; STR.tu.ics = STR.kn.ics;
  let lang = "en", busy = false, root, els = {};

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .fab { position: fixed; bottom: 20px; right: 20px; width: 58px; height: 58px; border-radius: 50%; border: 0; cursor: pointer;
           background: var(--c); color: #fff; font-size: 26px; box-shadow: 0 6px 20px rgba(0,0,0,.25); z-index: 2147483000; }
    :host(.left) .fab, :host(.left) .panel { right: auto; left: 20px; }
    .panel { position: fixed; bottom: 90px; right: 20px; width: 370px; max-width: calc(100vw - 24px); height: 560px; max-height: calc(100vh - 110px);
             display: flex; flex-direction: column; background: #fff; border-radius: 16px; overflow: hidden; color: #1d2b2f;
             box-shadow: 0 12px 40px rgba(0,0,0,.28); z-index: 2147483000; }
    .panel[hidden] { display: none; }
    .head { background: var(--c); color: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 10px; }
    .head img, .head .mark { width: 34px; height: 34px; border-radius: 50%; background: rgba(255,255,255,.25); object-fit: cover;
                             display: grid; place-items: center; font-weight: 700; }
    .head .t { flex: 1; min-width: 0; line-height: 1.2; }
    .head .t b { display: block; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .head .t span { font-size: 12px; opacity: .9; }
    select, .x { background: rgba(255,255,255,.2); color: #fff; border: 0; border-radius: 8px; padding: 5px 6px; font-size: 12px; cursor: pointer; }
    select option { color: #000; }
    .log { flex: 1; overflow-y: auto; padding: 14px; background: #f4f8f8; display: flex; flex-direction: column; gap: 8px; }
    .m { max-width: 86%; padding: 9px 12px; border-radius: 14px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; word-break: break-word; }
    .bot { background: #fff; border: 1px solid #dfe9ea; align-self: flex-start; border-bottom-left-radius: 4px; }
    .me { background: var(--c); color: #fff; align-self: flex-end; border-bottom-right-radius: 4px; }
    .alert { border: 2px solid #c62828; background: #fdecea; }
    .qr { display: flex; flex-wrap: wrap; gap: 6px; }
    .qr button, .chips button { border: 1px solid var(--c); color: var(--c); background: #fff; border-radius: 16px; padding: 6px 12px; font-size: 13px; cursor: pointer; }
    .qr button:hover, .chips button:hover { background: var(--c); color: #fff; }
    .chips[hidden] { display: none; }
    .qr a { background: var(--c); color: #fff; border-radius: 16px; padding: 6px 12px; font-size: 13px; text-decoration: none; }
    .chips { padding: 0 14px 8px; background: #f4f8f8; display: flex; flex-wrap: wrap; gap: 6px; }
    form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #dfe9ea; background: #fff; }
    input { flex: 1; border: 1px solid #c9d8da; border-radius: 20px; padding: 9px 14px; font-size: 14px; outline: none; }
    input:focus { border-color: var(--c); }
    form button { border: 0; background: var(--c); color: #fff; border-radius: 20px; padding: 0 16px; font-size: 14px; cursor: pointer; }
    button:disabled, input:disabled { opacity: .55; cursor: default; }
    .note { font-size: 11px; color: #5d7276; text-align: center; padding: 0 10px 8px; background: #fff; }
  `;

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  const S = () => STR[lang] || STR.en;

  const VOICE_LANG = { en: "en-IN", hi: "hi-IN", kn: "kn-IN", tu: "kn-IN" };
  let speakOn = false;
  function speakText(t) {
    if (!speakOn || !window.speechSynthesis) return;
    const clean = String(t).replace(/\[test mode\][^\n]*/gi, "").trim().slice(0, 400);
    if (!clean || (STR[lang] && clean === STR[lang].wait)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(clean);
    u.lang = VOICE_LANG[lang] || "en-IN";
    speechSynthesis.speak(u);
  }
  function addMsg(text, who, alert) {
    const node = addMsg0(text, who, alert);
    if (who === "bot" && text) speakText(text);
    return node;
  }
  function addMsg0(text, who, alert) {
    const m = el("div", "m " + who + (alert ? " alert" : ""), text);
    els.log.appendChild(m); els.log.scrollTop = els.log.scrollHeight; return m;
  }
  function clearButtons() { els.log.querySelectorAll(".qr").forEach((n) => n.remove()); els.chips.hidden = true; }
  function addButtons(items) {
    if (!items.length) return;
    const row = el("div", "qr");
    items.forEach((it) => { const b = el("button", "", it.label); b.type = "button"; b.onclick = () => send(it.send, it.label); row.appendChild(b); });
    els.log.appendChild(row); els.log.scrollTop = els.log.scrollHeight;
  }
  function addLinks(items) {
    if (!items.length) return;
    const row = el("div", "qr");
    items.forEach((it) => { const a = el("a", "", "📅 " + (S().ics || STR.en.ics)); a.href = API + it.href; a.target = "_blank"; a.rel = "noopener"; row.appendChild(a); });
    els.log.appendChild(row); els.log.scrollTop = els.log.scrollHeight;
  }
  function setBusy(v) { busy = v; els.input.disabled = v; els.sendBtn.disabled = v; }

  async function send(text, shown) {
    text = (text || "").trim();
    if (!text || busy) return;
    clearButtons(); addMsg(shown || text, "me"); setBusy(true);
    const typing = addMsg(S().wait, "bot");
    try {
      const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 45000);
      const res = await fetch(API + "/chat", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ session_id: SID, message: text, lang: lang }), signal: ctl.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error("http " + res.status);
      const d = await res.json();
      typing.remove(); addMsg(d.reply, "bot", d.emergency);
      addButtons(d.slots || []); addButtons(d.actions || []); addLinks(d.links || []);
    } catch (e) { typing.remove(); addMsg(S().err, "bot"); }
    setBusy(false); els.input.focus();
  }

  function renderChips() {
    els.chips.textContent = "";
    CHIPS.forEach((c, i) => {
      const b = el("button", "", (S().chips || STR.en.chips)[i]); b.type = "button";
      b.onclick = () => send(c + " soonest morning", b.textContent); els.chips.appendChild(b);
    });
  }
  function applyLang() {
    const s = S();
    els.title.textContent = els.name; els.sub.textContent = s.title; els.input.placeholder = s.ph;
    els.sendBtn.textContent = s.send; els.note.textContent = s.note; renderChips();
    const first = els.log.querySelector(".m.bot[data-greet]"); if (first) first.textContent = s.greet;
  }

  function mount(cfg) {
    const theme = cfg.theme || {};
    const color = ds.color || theme.color || theme.primary || "#0e7c86";
    const logo = ds.logo || theme.logo || "";
    els.name = ds.name || cfg.name || "Clinic";
    lang = STR[ds.lang] ? ds.lang : STR[cfg.default_lang] ? cfg.default_lang : "en";

    const host = document.createElement("div");
    if (ds.position === "left") host.className = "left";
    if (window.CSS && CSS.supports && CSS.supports("color", color)) host.style.setProperty("--c", color);
    else host.style.setProperty("--c", "#0e7c86");
    root = host.attachShadow({ mode: "open" });

    const style = document.createElement("style"); style.textContent = CSS; root.appendChild(style);
    const fab = el("button", "fab", "💬"); fab.setAttribute("aria-label", "Open chat");
    const panel = el("section", "panel"); panel.hidden = true; panel.setAttribute("role", "dialog");

    const head = el("div", "head");
    if (logo) { const img = el("img"); img.src = logo; img.alt = ""; head.appendChild(img); }
    else head.appendChild(el("div", "mark", els.name.charAt(0).toUpperCase()));
    const t = el("div", "t"); els.title = el("b"); els.sub = el("span"); t.append(els.title, els.sub); head.appendChild(t);
    const sel = el("select"); sel.setAttribute("aria-label", "Language");
    [["en", "EN"], ["hi", "हिं"], ["kn", "ಕನ್ನಡ"], ["tu", "ತುಳು"]].forEach(([v, l]) => { const o = el("option", "", l); o.value = v; sel.appendChild(o); });
    sel.value = lang; sel.onchange = () => { lang = sel.value; applyLang(); };
    const x = el("button", "x", "✕"); x.setAttribute("aria-label", "Close chat"); x.onclick = () => { panel.hidden = true; };
    head.append(sel, x);

    els.log = el("div", "log"); els.log.setAttribute("role", "log"); els.log.setAttribute("aria-live", "polite");
    els.chips = el("div", "chips");
    const form = el("form"); els.input = el("input"); els.input.autocomplete = "off"; els.input.maxLength = 1000;
    els.sendBtn = el("button"); els.sendBtn.type = "submit";
    form.append(els.input, els.sendBtn);
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mkBtn = (label, title) => {
      const b = el("button"); b.type = "button"; b.textContent = label; b.title = title; b.setAttribute("aria-label", title);
      b.style.cssText = "border:1px solid #0e7c86;background:#fff;color:#0e7c86;border-radius:50%;width:38px;height:38px;min-width:38px;cursor:pointer;font-size:16px;padding:0;margin-right:6px";
      return b;
    };
    const speakBtn = mkBtn("\uD83D\uDD0A", "Read replies aloud"); speakBtn.style.opacity = "0.5";
    speakBtn.onclick = () => {
      speakOn = !speakOn; speakBtn.style.opacity = speakOn ? "1" : "0.5";
      if (!speakOn && window.speechSynthesis) speechSynthesis.cancel();
    };
    form.insertBefore(speakBtn, els.input);
    if (SR) {
      const mic = mkBtn("\uD83C\uDFA4", "Speak your message");
      let rec = null, listening = false;
      mic.onclick = () => {
        if (listening && rec) { rec.stop(); return; }
        if (busy) return;
        rec = new SR(); rec.lang = VOICE_LANG[lang] || "en-IN"; rec.interimResults = false; rec.maxAlternatives = 1;
        rec.onstart = () => { listening = true; mic.style.background = "#0e7c86"; mic.style.color = "#fff"; };
        rec.onend = () => { listening = false; mic.style.background = "#fff"; mic.style.color = "#0e7c86"; };
        rec.onerror = (e) => { els.input.placeholder = "Mic: " + e.error; };
        rec.onresult = (e) => {
          const t = e.results[0][0].transcript;
          speakOn = true; speakBtn.style.opacity = "1";
          send(t);
        };
        rec.start();
      };
      form.insertBefore(mic, speakBtn);
    }
    form.onsubmit = (e) => { e.preventDefault(); const v = els.input.value; els.input.value = ""; send(v); };
    els.note = el("div", "note");

    panel.append(head, els.log, els.chips, form, els.note);
    root.append(fab, panel);
    document.body.appendChild(host);

    const greet = addMsg("", "bot"); greet.setAttribute("data-greet", "1");
    applyLang();
    fab.onclick = () => { panel.hidden = !panel.hidden; if (!panel.hidden) els.input.focus(); };
  }

  async function init() {
    let cfg = {};
    try { const r = await fetch(API + "/chat/config?clinic=" + encodeURIComponent(CLINIC)); if (r.ok) cfg = await r.json(); } catch (e) { /* offline config is fine */ }
    mount(cfg);
  }
  if (document.body) init(); else document.addEventListener("DOMContentLoaded", init);
})();
