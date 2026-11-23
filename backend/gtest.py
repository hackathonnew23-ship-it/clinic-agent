import json, time, urllib.request, urllib.error
key = ""
for l in open("../.env"):
    if l.startswith("GEMINI_API_KEY="):
        key = l.split("=", 1)[1].strip()
H = {"Content-Type": "application/json", "x-goog-api-key": key}
body = json.dumps({"contents": [{"parts": [{"text": "Say hi in 3 words"}]}]}).encode()
for m in ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]:
    t0 = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", data=body, headers=H), timeout=25)
        print(m, "OK in", round(time.time() - t0, 1), "s:", json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"], flush=True)
    except urllib.error.HTTPError as e:
        print(m, "HTTP", e.code, flush=True)
    except Exception as e:
        print(m, "ERR", type(e).__name__, flush=True)
