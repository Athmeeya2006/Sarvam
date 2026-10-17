"""End-to-end test against the LIVE server and the REAL Sarvam APIs (no mocks).

    uvicorn app.main:app --port 8000          # terminal 1
    python scripts/e2e_test.py                # terminal 2   (--base http://host:port to override)

Scammer audio is synthesised with Bulbul (male voice) and sent as real audio chunks through Saaras."""
from __future__ import annotations

import argparse
import base64
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import sarvam_client as sc  # noqa: E402

RESULTS: list[tuple[str, bool, str, float]] = []


def check(name: str, ok: bool, detail: str = "", t0: float | None = None) -> bool:
    RESULTS.append((name, bool(ok), detail, time.perf_counter() - t0 if t0 else 0.0))
    print(f"  {'✅' if ok else '❌'} {name}  {detail}")
    return ok


def script_lines(md: str, section: str | None = None) -> list[str]:
    text = (ROOT / "samples" / md).read_text(encoding="utf-8")
    if section:
        text = text.split(section, 1)[1]
    return [m.group(1) for m in re.finditer(r'^\d+\.\s.*?"(.+?)"\s*$', text, re.M)]


def tts_wav(text: str, lang: str) -> bytes:
    return base64.b64decode(sc.speak(text, lang, speaker="rahul" if lang == "hi-IN" else "vijay", pace=1.0)[0])


def is_wav(b64: str | None) -> bool:
    if not b64:
        return False
    raw = base64.b64decode(b64)
    return raw[:4] == b"RIFF" and len(raw) > 1024


def scam_run(c: httpx.Client, lang: str, lines: list[str], label: str) -> tuple[str, dict]:
    sid = c.post("/api/session/start", json={"user_lang": lang}).json()["session_id"]
    last, transcripts = {}, []
    t0 = time.perf_counter()
    for line in lines:
        r = c.post(f"/api/session/{sid}/chunk", files={"audio": ("chunk.wav", tts_wav(line, lang), "audio/wav")},
                   data={"mime": "audio/wav"})
        r.raise_for_status()
        last = r.json()
        transcripts.append(last.get("chunk_text", ""))
        ai = last.get("ai_result") or {}
        print(f"     heard[{last.get('detected_lang')}]: {last.get('chunk_text', '')[:70]}…  "
              f"instant={last.get('instant_score')} ai={ai.get('risk_score')}  {last.get('latencies_ms')}")
    ai = last.get("ai_result") or {}
    flags = [f for f in ai.get("red_flags", []) if f.get("evidence_quote")]
    kinds = {f["flag"] for f in flags}
    check(f"{label}: transcripts non-empty", all(transcripts), f"{len(transcripts)} chunks", t0)
    check(f"{label}: AI risk ≥ 70", ai.get("risk_score", 0) >= 70, f"risk={ai.get('risk_score')}")
    check(f"{label}: ≥ 3 red-flag types with quotes", len(kinds) >= 3, ", ".join(sorted(kinds)))
    return sid, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=120)

    print("\n1. Health")
    t0 = time.perf_counter()
    h = c.get("/api/health").json()
    check("health ok + key loaded", h.get("ok") and h.get("key_loaded"), str(h.get("models", {}).get("stt")), t0)
    check("health never returns the key", "sk_" not in str(h))

    print("\n2. Hindi scam call (Bulbul audio → Saaras → 105B)")
    sid, _ = scam_run(c, "hi-IN", script_lines("scam_script_hinglish.md")[:5], "hi scam")

    print("\n3. Warning audio")
    t0 = time.perf_counter()
    w = c.post(f"/api/session/{sid}/warn", json={"language": "hi-IN"}).json()
    check("warn returns WAV > 1 KB", is_wav(w.get("audio_base64")), w.get("warning_text", "")[:60], t0)
    check("warning mentions 1930", "1930" in w.get("warning_text", "") or "१९३०" in w.get("warning_text", ""))

    print("\n4. Dadi Mode")
    t0 = time.perf_counter()
    d = c.post(f"/api/session/{sid}/dadi/start").json()
    check("dadi/start returns opening audio", is_wav(d.get("dadi_audio_b64")), d.get("dadi_text", ""), t0)
    f = c.get("/api/fillers", params={"lang": "hi-IN"}).json()
    check("filler clips available", len(f.get("clips", [])) >= 4, f"{len(f.get('clips', []))} clips")
    time.sleep(1.1)
    line = "Dadi ji jaldi karo, mera UPI hai cbi.verify@okaxis aur number 9876543210, abhi paise bhejo"
    t0 = time.perf_counter()
    r = c.post(f"/api/session/{sid}/dadi/turn", files={"audio": ("t.wav", tts_wav(line, "hi-IN"), "audio/wav")},
               data={"mime": "audio/wav"})
    r.raise_for_status()
    t = r.json()
    print(f"     scammer: {t.get('scammer_text')}\n     dadi: {t.get('dadi_text')}  [{t.get('stalling_tactic')}]")
    values = {i["value"].lower() for i in t.get("evidence_locker", [])}
    upi_ok = "cbi.verify@okaxis" in values
    phone_ok = "9876543210" in values
    if not (upi_ok and phone_ok):  # ASR may mangle the handle; prove the locker logic with text mode too
        print("     (audio ASR missed an identifier; retrying the same line in text mode)")
        t = c.post("/api/demo/text", json={"session_id": sid, "text": line, "dadi": True}).json()
        values = {i["value"].lower() for i in t.get("evidence_locker", [])}
    check("evidence locker has UPI id", "cbi.verify@okaxis" in values, ", ".join(sorted(values)), t0)
    check("evidence locker has phone", "9876543210" in values)
    blocklist = ["9876543210", "cbi.verify", "50100"]
    dt = t.get("dadi_text", "")
    check("dadi_text non-empty & leaks no numbers", bool(dt) and not re.search(r"\d{3,}", dt)
          and not any(b in dt for b in blocklist), dt[:60])
    check("dadi reply audio", is_wav(t.get("dadi_audio_b64")))
    check("time_wasted_s > 0", t.get("time_wasted_s", 0) > 0, f"{t.get('time_wasted_s')} s")
    check("scam DNA present", bool(t.get("scam_dna", {}).get("playbook")), str(t.get("scam_dna")))

    print("\n5. End & report")
    t0 = time.perf_counter()
    e = c.post(f"/api/session/{sid}/end").json()
    check("complaint contains UPI id", "cbi.verify@okaxis" in (e.get("complaint_en") or "").lower(), "", t0)
    check("family alert written", bool(e.get("family_alert_local") and e.get("family_alert_en")))
    check("session deleted after end (privacy)", c.post(f"/api/session/{sid}/end").status_code == 404)

    print("\n6. Document check (Sarvam Vision → 105B → Bulbul)")
    t0 = time.perf_counter()
    with open(ROOT / "samples" / "fake_cbi_warrant.png", "rb") as fh:
        v = c.post("/api/verify-document", files={"image": ("fake_cbi_warrant.png", fh, "image/png")},
                   data={"language": "hi-IN"}).json()
    check("verdict likely_fake/suspicious", v.get("verdict") in {"likely_fake", "suspicious"},
          f"{v.get('verdict')} ({v.get('confidence')}%) {v.get('latencies_ms')}", t0)
    check("vision extracted fields", bool(v.get("fields")), ", ".join(list(v.get("fields", {}))[:4]))
    check("spoken verdict audio", is_wav(v.get("audio_base64")))

    print("\n7. Genuine bank call stays green")
    t0 = time.perf_counter()
    sid2 = c.post("/api/session/start", json={"user_lang": "hi-IN"}).json()["session_id"]
    last = {}
    for line in script_lines("safe_call_script.md")[:3]:
        last = c.post(f"/api/session/{sid2}/chunk", files={"audio": ("c.wav", tts_wav(line, "hi-IN"), "audio/wav")},
                      data={"mime": "audio/wav"}).json()
    risk = (last.get("ai_result") or {}).get("risk_score", 100)
    check("bank EMI call risk < 40", risk < 40, f"risk={risk}", t0)
    c.post(f"/api/session/{sid2}/end")

    print("\n8. Tamil scam call")
    sid3, _ = scam_run(c, "ta-IN", script_lines("scam_script_tamil.md"), "ta scam")
    t0 = time.perf_counter()
    w = c.post(f"/api/session/{sid3}/warn", json={"language": "ta-IN"}).json()
    tamil = bool(re.search(r"[஀-௿]", w.get("warning_text", "")))
    check("Tamil warning audio (ta-IN)", is_wav(w.get("audio_base64")) and w.get("language") == "ta-IN" and tamil,
          w.get("warning_text", "")[:60], t0)
    c.post(f"/api/session/{sid3}/end")

    passed = sum(ok for _, ok, _, _ in RESULTS)
    print("\n" + "=" * 78)
    print(f"{'Check':<45} {'Result':<8} {'Time':>7}")
    print("-" * 78)
    for name, ok, _, secs in RESULTS:
        print(f"{name:<45} {'✅ pass' if ok else '❌ FAIL':<8} {secs:>6.1f}s" if secs else
              f"{name:<45} {'✅ pass' if ok else '❌ FAIL':<8} {'':>7}")
    print("-" * 78)
    print(f"{passed}/{len(RESULTS)} checks passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
