"""Replay the demo scripts through the live server (text mode) and print the score after each line.
Usage: python scripts/eval_calls.py [--base http://localhost:8000] [--audio]
--audio synthesises each line with Bulbul, converts it to webm/opus (like the browser) and sends it through Saaras."""
import argparse
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def lines(md: str, header: str | None = None):
    text = (ROOT / "samples" / md).read_text()
    if header:
        text = text.split(header, 1)[1]
    return [m.group(1) for m in re.finditer(r'^\d+\.\s.*?"(.+?)"\s*$', text, re.M)]


def to_webm(wav: bytes, noise: bool) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "in.wav", Path(d) / "out.webm"
        src.write_bytes(wav)
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src)]
        if noise:  # fan / room noise at a fairly high level
            cmd += ["-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.08", "-filter_complex",
                    "[0:a]volume=0.8[a];[a][1:a]amix=inputs=2:duration=first[o]", "-map", "[o]"]
        cmd += ["-ac", "1", "-ar", "48000", "-c:a", "libopus", str(dst)]
        subprocess.run(cmd, check=True)
        return dst.read_bytes()


def run(base, name, utterances, lang="hi-IN", audio=False, noise=False):
    c = httpx.Client(base_url=base, timeout=90)
    sid = c.post("/api/session/start", json={"language": lang}).json()["session_id"]
    print(f"\n=== {name} ({'audio' + ('+noise' if noise else '') if audio else 'text'}) ===")
    for i, u in enumerate(utterances, 1):
        if audio:
            from app import sarvam_client as sc
            wav = base64.b64decode(sc.speak(u, "hi-IN", speaker="rahul")[0])
            r = c.post(f"/api/session/{sid}/chunk", files={"audio": ("c.webm", to_webm(wav, noise), "audio/webm")}).json()
            heard = (r.get("transcript_chunk") or {}).get("text", "")
        else:
            r = c.post("/api/demo/text", json={"session_id": sid, "text": u}).json()
            heard = u
        ai = r.get("ai_result") or {}
        print(f"{i}. instant={r.get('instant_score', '-'):>3}  ai={ai.get('risk_score', '-'):>3}  "
              f"peak={r.get('peak_score'):>3}  flags={len(ai.get('red_flags', []))}  {r.get('latencies')}\n   heard: {heard[:110]}")
    if (ai.get("warning_text")):
        print("   warning:", ai["warning_text"])
    return r.get("peak_score")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--audio", action="store_true")
    ap.add_argument("--noise", action="store_true")
    a = ap.parse_args()
    scam = run(a.base, "SCAM (digital arrest)", lines("scam_script_hinglish.md"), audio=a.audio, noise=a.noise)
    safe = run(a.base, "GENUINE bank EMI reminder", lines("safe_call_script.md", None)[:5], audio=a.audio, noise=a.noise)
    fam = run(a.base, "GENUINE family call", lines("safe_call_script.md", "# Family call"), audio=a.audio, noise=a.noise)
    print(f"\nRESULT  scam peak={scam}  bank peak={safe}  family peak={fam}")
