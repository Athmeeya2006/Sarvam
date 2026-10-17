"""Drive the real website in Chrome (Playwright) through the full demo and save screenshots.

    pip install playwright && playwright install chromium     # one time
    uvicorn app.main:app --port 8000                          # terminal 1
    python scripts/browser_test.py                            # terminal 2

Part A uses a FAKE MICROPHONE: Chrome plays samples/mic_scam_call.wav (Bulbul-voiced scam lines) as mic input, so the
real MediaRecorder -> Saaras -> Sarvam-105B path is exercised. Part B drives text mode, Dadi Mode, the report and the
document check."""
from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOTS = ROOT / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OK: list[tuple[str, bool]] = []


def check(name, fn):
    try:
        fn()
        OK.append((name, True))
        print("  ✅", name)
    except Exception as e:  # noqa: BLE001
        OK.append((name, False))
        print("  ❌", name, "->", str(e).splitlines()[0][:200])


def mic_wav() -> Path:
    """Scam lines voiced by Bulbul, joined with pauses, as 48 kHz mono WAV for Chrome's fake mic."""
    out = ROOT / "samples" / "mic_scam_call.wav"
    if out.exists():
        return out
    from app import sarvam_client as sc
    lines = ["Hello, main Mumbai CBI cyber cell se Inspector Vikram Singh bol raha hoon.",
             "Aapke Aadhaar card se ek parcel mila hai jismein drugs hain, aapke naam par arrest warrant hai.",
             "Ye case confidential hai, kisi ko mat batana, video call par rahiye, aap digital arrest mein hain.",
             "RBI verification ke liye saare paise safe account mein transfer kariye, abhi."]
    with tempfile.TemporaryDirectory() as d:
        parts = []
        for i, line in enumerate(lines):
            p = Path(d) / f"{i}.wav"
            p.write_bytes(base64.b64decode(sc.speak(line, "hi-IN", speaker="rahul", pace=1.0)[0]))
            parts.append(p)
        inputs = sum([["-i", str(p)] for p in parts], [])
        filt = "".join(f"[{i}:a]apad=pad_dur=1.5[a{i}];" for i in range(len(parts)))
        filt += "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", filt, "-map", "[o]",
                        "-ar", "48000", "-ac", "1", str(out)], check=True)
    return out


def main():
    wav = mic_wav()
    errors: list[str] = []
    with sync_playwright() as pw:
        args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"]
        try:
            browser = pw.chromium.launch(args=args)
        except Exception:  # noqa: BLE001
            browser = pw.chromium.launch(channel="chrome", args=args)
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, permissions=["microphone"])
        page = ctx.new_page()
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))

        print("\nA. Real microphone path (fake mic plays a Bulbul-voiced scam call)")
        page.goto(BASE)
        check("page loads with title", lambda: expect(page).to_have_title("Kavach · कवच"))
        page.screenshot(path=SHOTS / "01_home.png")
        page.click("#protectBtn")
        check("mic starts listening", lambda: expect(page.locator("#status")).to_contain_text("Listening", timeout=5000))
        check("speech transcribed from the mic", lambda: expect(page.locator("#transcript .pop").first).to_be_visible(timeout=40000))
        check("red takeover appears from live audio", lambda: expect(page.locator("#takeover")).to_be_visible(timeout=90000))
        check("warning text shown", lambda: expect(page.locator("#warnText")).not_to_be_empty(timeout=20000))
        check("warning audio loaded", lambda: page.wait_for_function("document.querySelector('#player').src.startsWith('data:audio/wav')", timeout=20000))
        page.screenshot(path=SHOTS / "02_takeover.png")
        page.click("#dismissBtn")
        page.screenshot(path=SHOTS / "03_detect.png", full_page=True)
        check("red flags rendered with quotes", lambda: expect(page.locator("#flags mark").first).to_be_visible())

        print("\nB. Dadi Mode, report and document check (text mode)")
        page.click("#takeoverDadi", force=True) if page.locator("#takeover").is_visible() else page.click("#dadiCta")
        check("Dadi view opens", lambda: expect(page.locator("#dadiView")).to_be_visible(timeout=20000))
        check("Dadi opening bubble", lambda: expect(page.locator(".bubble-dadi").first).to_be_visible(timeout=20000))
        page.click("#protectBtn")  # stop the fake mic so text turns are deterministic
        page.click(".dadi-demo >> nth=0")
        check("Dadi replies to UPI line", lambda: expect(page.locator(".bubble-dadi")).to_have_count(2, timeout=45000))
        check("UPI lands in Evidence Locker", lambda: expect(page.locator("#locker")).to_contain_text("cbi.verify@okaxis", timeout=10000))
        page.wait_for_timeout(1500)
        page.click(".dadi-demo >> nth=1")
        check("phone number lands in locker", lambda: expect(page.locator("#locker")).to_contain_text("9876543210", timeout=45000))
        t1 = page.locator("#timer").inner_text()
        page.wait_for_timeout(2200)
        t2 = page.locator("#timer").inner_text()
        check("time-wasted counter ticks", lambda: (_ for _ in ()).throw(AssertionError(f"{t1}->{t2}")) if t1 == t2 else None)
        page.screenshot(path=SHOTS / "04_dadi_mode.png", full_page=True)
        page.click("#dadiEnd")
        check("report shows complaint with UPI", lambda: expect(page.locator("#cEn")).to_contain_text("cbi.verify@okaxis", timeout=60000))
        check("family alert labelled simulated", lambda: expect(page.locator("#endPanel")).to_contain_text("SIMULATED"))
        page.screenshot(path=SHOTS / "05_report.png", full_page=True)

        page.goto(BASE)
        page.click("#useSample")
        check("document verdict FAKE", lambda: expect(page.locator("#docResult")).to_contain_text("LIKELY FAKE", timeout=90000))
        page.locator("#docResult").scroll_into_view_if_needed()
        page.screenshot(path=SHOTS / "06_document.png", full_page=True)

        page.goto(BASE)
        page.check("#textModeToggle")
        page.click(".demo-script[data-script=safe]")
        page.wait_for_function("!document.querySelector('.demo-script').disabled", timeout=90000)
        score = int(page.locator("#aiVal").inner_text() or 0)
        check(f"genuine bank call stays green (AI={score})", lambda: (_ for _ in ()).throw(AssertionError(score)) if score >= 40 or page.locator("#takeover").is_visible() else None)
        page.screenshot(path=SHOTS / "07_genuine_call.png", full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=SHOTS / "08_mobile.png", full_page=True)
        no_hscroll = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        check("mobile: no horizontal scroll", lambda: None if no_hscroll else (_ for _ in ()).throw(AssertionError("overflow")))
        real_errors = [e for e in errors if "favicon" not in e]
        check("no JavaScript errors", lambda: None if not real_errors else (_ for _ in ()).throw(AssertionError(real_errors[:3])))
        browser.close()

    passed = sum(ok for _, ok in OK)
    print(f"\n{passed}/{len(OK)} browser checks passed · screenshots in docs/screenshots/")
    sys.exit(0 if passed == len(OK) else 1)


if __name__ == "__main__":
    main()
