"""Kavach API. Run locally with uvicorn (see README) or deploy to Vercel (app/main.py is the entrypoint)."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, dadi, evidence, prompts, risk, store
from . import sarvam_client as sc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kavach")

if not os.getenv("VERCEL"):
    config.get_api_key()  # local: fail fast with a helpful message if the key is missing

MAX_AUDIO_BYTES = 3 * 1024 * 1024
WARN_COOLDOWN_S = 20
POOL = ThreadPoolExecutor(max_workers=8)
STORE = store.make_store()
log.info("session store: %s", STORE.kind)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dadi filler clips ship pre-generated in static/fillers/. If they are missing, warm them in the background.
    if not dadi.fillers_on_disk():
        threading.Thread(target=dadi.warm_fillers, daemon=True).start()
    yield


app = FastAPI(title="Kavach · कवच", version="2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")
app.mount("/samples", StaticFiles(directory=config.ROOT / "samples"), name="samples")


@dataclass
class Session:
    id: str
    language: str = "hi-IN"
    started: float = field(default_factory=time.time)
    chunks: list[dict] = field(default_factory=list)
    ai_result: dict | None = None
    ai_for_chars: int = 0
    peak_score: int = 0
    flags: dict[str, dict] = field(default_factory=dict)
    last_warn: float = 0
    warn_cache: dict[str, str] = field(default_factory=dict)
    # Dadi Mode
    dadi_on: bool = False
    dadi_started: float = 0
    dadi_lang: str = "hi-IN"
    history: list[dict] = field(default_factory=list)
    locker: evidence.Locker = field(default_factory=evidence.Locker)
    scam_dna: dict = field(default_factory=lambda: {"playbook": "unclear", "stage": "hook"})
    last_scammer_lang: str | None = None

    FIELDS = ("language", "started", "chunks", "ai_result", "ai_for_chars", "peak_score", "flags", "last_warn",
              "warn_cache", "dadi_on", "dadi_started", "dadi_lang", "history", "locker", "scam_dna",
              "last_scammer_lang")

    @property
    def transcript(self) -> str:
        return " ".join(c["text"] for c in self.chunks if c["text"])

    @property
    def time_wasted_s(self) -> int:
        return int(time.time() - self.dadi_started) if self.dadi_started else 0

    def dump(self, *names: str) -> dict:
        return {n: (self.locker.items if n == "locker" else getattr(self, n)) for n in (names or self.FIELDS)}

    @classmethod
    def from_dict(cls, sid: str, d: dict) -> Session:
        s = cls(id=sid)
        for n in cls.FIELDS:
            if n in d:
                setattr(s, n, evidence.Locker.from_items(d[n]) if n == "locker" else d[n])
        return s


# Sessions live in STORE (memory locally, Upstash Redis on Vercel), never in a module-level dict, so any
# serverless instance can serve any request of a call.
def _get(sid: str) -> Session:
    d = STORE.get(sid)
    if not d:
        raise HTTPException(404, "Session not found or already ended. Start a new one.")
    return Session.from_dict(sid, d)


def _save(s: Session, *names: str) -> None:
    STORE.update(s.id, s.dump(*names))


@contextmanager
def _locked(name: str, ttl_s: float = 15, wait_s: float = 30):
    """Cross-instance mutex (Redis SET NX in serverless, a dict locally)."""
    deadline = time.time() + wait_s
    while not (token := STORE.lock(name, ttl_s)):
        if time.time() > deadline:
            raise HTTPException(409, "Kavach is busy with this call, please retry")
        time.sleep(0.05)
    try:
        yield
    finally:
        STORE.unlock(name, token)


def _mutate(sid: str, fn, *names: str):
    """Load -> change -> save only the changed fields, under a short per-session write lock."""
    with _locked(f"w:{sid}"):
        s = _get(sid)
        result = fn(s)
        _save(s, *names)
        return s, result


def _lang(code: str | None) -> str:
    if code not in config.LANGUAGES:
        raise HTTPException(400, f"Unsupported language {code}")
    return code


async def _stt(audio: UploadFile, mime: str | None) -> dict:
    data = await audio.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio chunk too large (max ~30 s)")
    if len(data) < 1500:
        return {"transcript": "", "language_code": None, "ms": 0, "empty": True}
    try:
        return await asyncio.to_thread(sc.transcribe, data, audio.filename or "chunk.webm", mime or audio.content_type)
    except sc.SarvamError as e:
        raise HTTPException(502, f"Saaras could not transcribe this chunk: {e}") from e


# ------------------------------------------------------------------ Act 1: detect
def _analyse(sid: str, text: str, language: str) -> tuple[dict | None, int | None, bool]:
    """Sarvam-105B on the last ~60 s of transcript. Debounced: at most one call in flight per session
    (a store lock, so this holds across serverless instances too)."""
    if len(text) < 15:
        return None, None, False
    token = STORE.lock(f"llm:{sid}", 60)
    if not token:
        return None, None, True
    try:
        window = text[-config.TRANSCRIPT_WINDOW_CHARS:]
        lang = config.LANGUAGES[language]
        raw, ms = sc.chat_json(prompts.RISK_SYSTEM.format(lang_name=lang["english"], lang_code=language),
                               prompts.risk_user(window), schema=prompts.RISK_SCHEMA)
        result = risk.normalise_ai(raw)
        result["explanation_en"] = raw.get("explanation_en", "")
        result["red_flags"] = risk.verify_evidence(result["red_flags"], window)

        def apply(s: Session):
            if len(text) >= s.ai_for_chars:  # never replace a newer analysis with an older one
                s.ai_result, s.ai_for_chars = result, len(text)
                if result["warning_text"]:
                    s.warn_cache = {language: result["warning_text"]}
            s.peak_score = max(s.peak_score, result["risk_score"])
            for f in result["red_flags"]:
                s.flags[f["flag"]] = f

        _mutate(sid, apply, "ai_result", "ai_for_chars", "warn_cache", "peak_score", "flags")
        return result, ms, False
    finally:
        STORE.unlock(f"llm:{sid}", token)


def _ingest_text(sid: str, text: str, detected: str | None, stt_ms: int | None, mode: str) -> dict:
    latencies = {"saaras": stt_ms} if stt_ms is not None else {}

    def append(s: Session):
        chunk = {"text": text, "lang": detected, "t": round(time.time() - s.started, 1), "mode": mode}
        s.chunks.append(chunk)
        if detected:
            s.last_scammer_lang = detected
        inst = risk.instant_score(s.transcript[-config.TRANSCRIPT_WINDOW_CHARS:])
        for f in inst["flags"]:
            s.flags.setdefault(f["flag"], f)  # never overwrite an AI-verified flag
        return chunk, inst

    s, (chunk, inst) = _mutate(sid, append, "chunks", "last_scammer_lang", "flags")

    ai_error, ai, skipped = None, None, False
    try:
        ai, ms, skipped = _analyse(sid, s.transcript, s.language)
        if ms is not None:
            latencies["sarvam-105b"] = ms
    except sc.SarvamError as e:
        ai_error = str(e)
    if ai is not None:
        s = _get(sid)  # pick up the merged AI result

    return {
        "chunk_text": text,
        "transcript_chunk": chunk,
        "full_transcript": s.transcript,
        "detected_lang": detected,
        "instant_score": inst["score"],
        "ai_result": s.ai_result,
        "ai_fresh": ai is not None,
        "ai_skipped_busy": skipped,
        "ai_error": ai_error,
        "peak_score": s.peak_score,
        "flags": list(s.flags.values()),
        "threshold": config.RISK_THRESHOLD,
        "latencies_ms": latencies,
    }


# ------------------------------------------------------------------ Act 3: Dadi
def _dadi_turn(sid: str, scammer_text: str, detected: str | None, stt_ms: int | None, mode: str) -> dict:
    # One Dadi turn at a time per call, across all instances. Reload inside the lock to see the latest history.
    with _locked(f"dadi:{sid}", ttl_s=90, wait_s=60):
        s = _get(sid)
        latencies = {"saaras": stt_ms} if stt_ms is not None else {}
        t = time.time() - s.dadi_started
        s.chunks.append({"text": scammer_text, "lang": detected, "t": round(time.time() - s.started, 1),
                         "mode": mode, "dadi": True})
        lang = dadi.persona_lang(detected or s.last_scammer_lang or s.dadi_lang)
        s.dadi_lang = lang

        # 1. regex evidence: instant + exact
        new = s.locker.add(evidence.regex_extract(scammer_text), t)

        # 2. in parallel: 105B evidence extraction + Dadi's reply
        convo = "\n".join(f"{h['role']}: {h['text']}" for h in s.history[-10:])
        ev_future = POOL.submit(
            sc.chat_json, prompts.EVIDENCE_SYSTEM,
            f"Conversation so far:\n{convo}\n\nSCAMMER'S LATEST LINE:\n<<<\n{scammer_text}\n>>>",
            600, prompts.EVIDENCE_SCHEMA, 0.1, "evidence")
        dadi_future = POOL.submit(dadi.reply, s.history, scammer_text, lang, s.locker.collected_kinds())

        try:
            out, ms = dadi_future.result(timeout=40)
            latencies["sarvam-105b"] = ms
        except Exception as e:  # noqa: BLE001 - keep the decoy talking whatever happens
            log.warning("dadi reply failed: %s", e)
            out = {"reply_text": prompts.FILLERS[lang][4], "stalling_tactic": "mishearing", "evidence_asked_for": "none"}
        try:
            ev, ms = ev_future.result(timeout=40)
            latencies["evidence"] = ms
            items = [dict(i, source="ai") for i in ev.get("items", []) if i.get("value")]
            # explainability: the quote must come from the scammer's line
            items = [i for i in items if not i.get("quote") or risk.verify_evidence(
                [{"evidence_quote": i["quote"]}], scammer_text)]
            new += s.locker.add(items, t)
            if ev.get("scam_dna"):
                s.scam_dna = ev["scam_dna"]
        except Exception as e:  # noqa: BLE001
            log.warning("evidence extraction failed: %s", e)

        # 3. Bulbul speaks as Dadi
        audio = None
        try:
            audio, ms = sc.speak(out["reply_text"], lang, speaker=dadi.voice(lang), pace=config.DADI_PACE)
            latencies["bulbul"] = ms
        except sc.SarvamError as e:
            log.warning("dadi tts failed: %s", e)

        s.history += [{"role": "scammer", "text": scammer_text}, {"role": "dadi", "text": out["reply_text"]}]
        new_chunks = s.chunks[-1:]
        merged_locker = s.locker

        def save(cur: Session):
            cur.chunks += new_chunks
            cur.dadi_lang, cur.history, cur.scam_dna = s.dadi_lang, s.history, s.scam_dna
            cur.locker = merged_locker

        _mutate(sid, save, "chunks", "dadi_lang", "history", "scam_dna", "locker")
        return {
            "scammer_text": scammer_text,
            "detected_lang": detected,
            "dadi_text": out["reply_text"],
            "dadi_lang": lang,
            "dadi_audio_b64": audio,
            "stalling_tactic": out.get("stalling_tactic", ""),
            "evidence_asked_for": out.get("evidence_asked_for", "none"),
            "new_evidence": new,
            "evidence_locker": s.locker.items,
            "time_wasted_s": s.time_wasted_s,
            "scam_dna": s.scam_dna,
            "latencies_ms": latencies,
        }


# ------------------------------------------------------------------ routes
class StartBody(BaseModel):
    user_lang: str | None = None
    language: str | None = None  # alias


class TextBody(BaseModel):
    session_id: str
    text: str
    dadi: bool = False
    lang: str | None = None  # optional language hint for text mode (e.g. ta-IN)


class LangBody(BaseModel):
    language: str | None = None


@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "key_loaded": bool(config.get_api_key(required=False)),
            "store": STORE.kind, "serverless": bool(os.getenv("VERCEL")),
            "models": {"stt": config.STT_MODEL, "stt_fallback": config.STT_FALLBACK_MODEL, "llm": config.LLM_MODEL,
                       "tts": config.TTS_MODEL, "vision": "sarvam-vision (doc_ai extract + digitise)"},
            "chunk_seconds": config.CHUNK_SECONDS, "threshold": config.RISK_THRESHOLD,
            "languages": {k: v["name"] for k, v in config.LANGUAGES.items()}}


@app.post("/api/session/start")
def start(body: StartBody | None = None):
    lang = _lang((body and (body.user_lang or body.language)) or "hi-IN")
    s = Session(id=uuid.uuid4().hex[:12], language=lang, dadi_lang=dadi.persona_lang(lang))
    _save(s)
    return {"session_id": s.id, "language": s.language, "threshold": config.RISK_THRESHOLD}


@app.post("/api/session/{sid}/language")
def set_language(sid: str, body: LangBody):
    lang = _lang(body.language) if body.language else None

    def apply(s: Session):
        s.language = lang or s.language

    s, _ = _mutate(sid, apply, "language")
    return {"language": s.language}


@app.post("/api/session/{sid}/chunk")
async def chunk(sid: str, audio: UploadFile = File(...), mime: str | None = Form(None)):
    s = _get(sid)
    stt = await _stt(audio, mime)
    if not stt["transcript"]:
        return {"skipped": "no_speech", "full_transcript": s.transcript, "ai_result": s.ai_result,
                "instant_score": None, "peak_score": s.peak_score, "flags": list(s.flags.values()),
                "latencies_ms": {"saaras": stt["ms"]} if stt["ms"] else {}}
    return await asyncio.to_thread(_ingest_text, sid, stt["transcript"], stt["language_code"], stt["ms"], "audio")


@app.post("/api/demo/text")
async def demo_text(body: TextBody):
    """Text mode: same pipeline without the microphone (also drives Dadi turns)."""
    s = _get(body.session_id)
    text = body.text.strip()[:1000]
    if not text:
        raise HTTPException(400, "Empty text")
    if body.dadi:
        if not s.dadi_on:
            raise HTTPException(400, "Dadi Mode is not active. Call /dadi/start first.")
        return await asyncio.to_thread(_dadi_turn, s.id, text, body.lang, None, "text")
    return await asyncio.to_thread(_ingest_text, s.id, text, body.lang, None, "text")


@app.post("/api/session/{sid}/warn")
def warn(sid: str, body: LangBody | None = None, force: bool = False):
    s = _get(sid)
    lang = _lang((body.language if body else None) or s.language)
    now = time.time()
    if not force and now - s.last_warn < WARN_COOLDOWN_S:
        return JSONResponse({"skipped": "cooldown", "retry_in": int(WARN_COOLDOWN_S - (now - s.last_warn))}, 429)
    latencies = {}
    text = s.warn_cache.get(lang)
    if not text:
        scam = (s.ai_result or {}).get("scam_type", "digital_arrest")
        raw, ms = sc.chat_json(prompts.WARN_SYSTEM.format(lang_name=config.LANGUAGES[lang]["english"],
                                                          lang_code=lang, scam_type=scam),
                               "Write the warning.", max_tokens=300)
        latencies["sarvam-105b"] = ms
        text = raw.get("warning_text") or ""
        if not text:
            raise HTTPException(502, "Could not generate a warning")
        s.warn_cache[lang] = text
        _mutate(sid, lambda cur: cur.warn_cache.update({lang: text}), "warn_cache")
    try:
        audio, ms = sc.speak(text, lang, pace=config.WARN_PACE)
    except sc.SarvamError as e:
        raise HTTPException(502, str(e)) from e
    latencies["bulbul"] = ms
    _mutate(sid, lambda cur: setattr(cur, "last_warn", now), "last_warn")
    return {"warning_text": text, "language": lang, "audio_base64": audio, "mime": "audio/wav",
            "latencies_ms": latencies}


@app.get("/api/fillers")
async def get_fillers(lang: str = "hi-IN"):
    clips = await asyncio.to_thread(dadi.fillers, lang)
    return {"lang": dadi.persona_lang(lang), "clips": clips}


@app.post("/api/session/{sid}/dadi/start")
async def dadi_start(sid: str):
    def apply(s: Session):
        s.dadi_on, s.dadi_lang = True, dadi.persona_lang(s.last_scammer_lang or s.language)
        if not s.dadi_started:
            s.dadi_started = time.time()

    s, _ = _mutate(sid, apply, "dadi_on", "dadi_lang", "dadi_started")
    lang = s.dadi_lang
    p = prompts.PERSONAS[lang]
    try:
        audio, ms = await asyncio.to_thread(sc.speak, p["opening"], lang, dadi.voice(lang), config.DADI_PACE)
    except sc.SarvamError as e:
        raise HTTPException(502, str(e)) from e
    _mutate(sid, lambda cur: cur.history.append({"role": "dadi", "text": p["opening"]}), "history")
    return {"persona": p["name"], "dadi_text": p["opening"], "dadi_lang": lang, "dadi_audio_b64": audio,
            "time_wasted_s": s.time_wasted_s, "latencies_ms": {"bulbul": ms}}


@app.post("/api/session/{sid}/dadi/turn")
async def dadi_turn(sid: str, audio: UploadFile = File(...), mime: str | None = Form(None)):
    s = _get(sid)
    if not s.dadi_on:
        raise HTTPException(400, "Dadi Mode is not active")
    stt = await _stt(audio, mime)
    if not stt["transcript"]:
        return {"skipped": "no_speech", "evidence_locker": s.locker.items, "time_wasted_s": s.time_wasted_s,
                "scam_dna": s.scam_dna, "latencies_ms": {"saaras": stt["ms"]} if stt["ms"] else {}}
    return await asyncio.to_thread(_dadi_turn, sid, stt["transcript"], stt["language_code"], stt["ms"], "audio")


@app.post("/api/verify-document")
async def verify_document(image: UploadFile = File(...), language: str = Form("hi-IN")):
    lang = _lang(language)
    data = await image.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 15 MB)")
    name = image.filename or "document.png"
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".pdf")):
        name += ".jpg"

    def work():
        latencies = {}
        # Sarvam Vision: structured extract (with per-field confidence) and full digitise, in parallel.
        f_extract = POOL.submit(sc.extract_fields, data, name, prompts.NOTICE_SCHEMA)
        f_digitise = POOL.submit(sc.read_document, data, name, "hi-IN" if lang == "en-IN" else lang)
        fields, conf, text = {}, {}, ""
        try:
            fields, conf, ms = f_extract.result(timeout=90)
            latencies["vision"] = ms
        except Exception as e:  # noqa: BLE001
            log.warning("vision extract failed: %s", e)
        try:
            text, ms = f_digitise.result(timeout=90)
            latencies["vision"] = max(latencies.get("vision", 0), ms)
        except Exception as e:  # noqa: BLE001
            log.warning("vision digitise failed: %s", e)
        if not fields and len(text.strip()) < 10:
            raise HTTPException(422, "Sarvam Vision could not read any text in this image. Try a clearer photo.")
        lname = config.LANGUAGES[lang]["english"]
        field_lines = "\n".join(f"- {k}: {v!r} (confidence {conf.get(k)})" for k, v in fields.items())
        verdict, ms = sc.chat_json(prompts.DOC_SYSTEM.format(lang_name=lname, lang_code=lang),
                                   f"EXTRACTED FIELDS:\n{field_lines or '- none'}\n\nOCR TEXT:\n<<<\n{text[:6000]}\n>>>",
                                   max_tokens=1500, schema=prompts.DOC_SCHEMA)
        latencies["sarvam-105b"] = ms
        v = verdict.get("verdict", "suspicious")
        if v not in {"likely_fake", "suspicious", "looks_genuine"}:
            v = "suspicious"
        # Hard rule: an "official" notice that demands money / gives a payment account is fake. Always.
        pay = fields.get("payment_amount_demanded") or fields.get("payment_method_or_account")
        if pay and v != "likely_fake":
            v = "likely_fake"
            verdict.setdefault("reasons", []).insert(0, {
                "reason_en": "An official-looking notice that demands a payment is always a scam.",
                "reason_local": "पैसे माँगने वाला कोई भी 'सरकारी' नोटिस हमेशा धोखा होता है।", "evidence": str(pay)})
        summary = verdict.get("spoken_summary") or ""
        audio = None
        if summary:
            try:
                audio, ms = sc.speak(summary, lang)
                latencies["bulbul"] = ms
            except sc.SarvamError:
                pass
        return {"verdict": v, "confidence": verdict.get("confidence"), "reasons": verdict.get("reasons", [])[:5],
                "spoken_summary": summary, "audio_base64": audio, "fields": fields, "field_confidence": conf,
                "ocr_text": text[:4000], "latencies_ms": latencies}

    return await asyncio.to_thread(work)


@app.post("/api/session/{sid}/end")
def end(sid: str):
    s = _get(sid)
    lang = config.LANGUAGES[s.language]
    flags = list(s.flags.values())
    out = {"peak_score": s.peak_score, "scam_type": (s.ai_result or {}).get("scam_type", "none"),
           "flags": flags, "evidence_locker": s.locker.items, "scam_dna": s.scam_dna,
           "time_wasted_s": s.time_wasted_s, "duration_s": int(time.time() - s.started), "latencies_ms": {}}
    if s.transcript.strip():
        flag_lines = "\n".join(f"- {f['label_en']}: \"{f['evidence_quote']}\"" for f in flags) or "- none"
        ev_lines = "\n".join(f"- {i['kind']}: {i['value']} (said: \"{i['quote']}\")" for i in s.locker.items) or "- none"
        try:
            summary, ms = sc.chat_json(
                prompts.END_SYSTEM.format(lang_name=lang["english"], lang_code=s.language),
                f"Call time: {datetime.fromtimestamp(s.started).strftime('%d %b %Y, %I:%M %p')}\n"
                f"Peak risk score: {s.peak_score}/100\nScam playbook: {s.scam_dna}\n"
                f"Scammer time wasted by Kavach decoy: {s.time_wasted_s} s\n"
                f"Red flags:\n{flag_lines}\n\nEVIDENCE LOCKER:\n{ev_lines}\n\n"
                f"TRANSCRIPT:\n<<<\n{s.transcript[-5000:]}\n>>>",
                max_tokens=2500, schema=prompts.END_SCHEMA)
            out.update({k: summary.get(k, "") for k in
                        ("family_alert_local", "family_alert_en", "complaint_local", "complaint_en")})
            out["latencies_ms"]["sarvam-105b"] = ms
            # Guarantee every hard identifier is in the complaint, even if the LLM dropped one.
            missing = [i for i in s.locker.items if i["kind"] in {"upi_id", "phone_number", "bank_account", "ifsc"}
                       and i["value"] not in out["complaint_en"]]
            if missing:
                out["complaint_en"] += "\n\nEvidence identifiers:\n" + "\n".join(
                    f"- {i['kind'].replace('_', ' ')}: {i['value']}" for i in missing)
        except sc.SarvamError as e:
            out["error"] = str(e)
    # Privacy: transcripts and evidence are deleted from the store when the call ends.
    STORE.delete(sid)
    return out
