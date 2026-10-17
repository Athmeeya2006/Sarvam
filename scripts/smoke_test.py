"""Call every Sarvam model family once and print latency.  Run:  python scripts/smoke_test.py"""
import base64
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import sarvam_client as sc  # noqa: E402

logging.basicConfig(level=logging.WARNING)
ok = True


def step(name, fn):
    global ok
    t0 = time.perf_counter()
    try:
        out = fn()
        print(f"  ✓ {name:<14} {int((time.perf_counter() - t0) * 1000):>6} ms   {str(out)[:140]}")
        return out
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  ✗ {name:<14} FAILED: {e}")


print("Kavach smoke test")
speech = "Main CBI se bol raha hoon. Aapke Aadhaar se ek parcel mein drugs mile hain. Kisi ko mat batana."
audio_b64 = step("bulbul", lambda: sc.speak(speech, "hi-IN")[0])
if audio_b64:
    wav = base64.b64decode(audio_b64)
    (ROOT / "samples" / "tts_scam_line.wav").write_bytes(wav)
    step("saaras", lambda: sc.transcribe(wav, "line.wav"))
step("sarvam-105b", lambda: sc.chat_json('Reply JSON {"ok": true, "lang": "<hindi word for yes>"}', "ping")[0])
warrant = ROOT / "samples" / "fake_cbi_warrant.png"
if warrant.exists():
    step("vision", lambda: sc.read_document(warrant.read_bytes(), warrant.name)[0][:160].replace("\n", " ") + "…")
else:
    print("  - vision skipped (run scripts/make_fake_warrant.py first)")
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
