"""One function per Sarvam model family. Each has a timeout, one retry and latency logging.

Saaras  (speech-to-text)     -> transcribe()
Sarvam-105B (chat)           -> chat_json()
Bulbul  (text-to-speech)     -> speak()
Sarvam Vision (Document AI)  -> read_document()
"""
from __future__ import annotations

import json
import logging
import re
import time
from functools import lru_cache
from typing import Any, Callable

import httpx
from sarvamai import SarvamAI

from . import config

log = logging.getLogger("kavach.sarvam")


class SarvamError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _sdk() -> SarvamAI:
    return SarvamAI(api_subscription_key=config.get_api_key(), timeout=config.HTTP_TIMEOUT)


def _timed(name: str, fn: Callable[[], Any], retries: int = 1) -> tuple[Any, int]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            out = fn()
            ms = int((time.perf_counter() - t0) * 1000)
            log.info("%s ok in %d ms", name, ms)
            return out, ms
        except Exception as e:  # noqa: BLE001 - surface any SDK/network failure uniformly
            last = e
            # Never include the key in logs; SDK errors don't contain it, but be safe.
            msg = str(e).replace(config.get_api_key(required=False) or "\0", "***")
            log.warning("%s failed (attempt %d): %s", name, attempt + 1, msg[:300])
            if attempt < retries:
                time.sleep(0.6)
    raise SarvamError(f"{name} failed: {str(last)[:200]}")


# ---------------------------------------------------------------- Saaras
def _codec(filename: str, mime: str | None) -> str:
    m = (mime or "").lower()
    name = filename.lower()
    if "mp4" in m or "m4a" in m or name.endswith((".mp4", ".m4a")):
        return "mp4"
    if "ogg" in m or name.endswith(".ogg"):
        return "ogg"
    if "wav" in m or name.endswith(".wav"):
        return "wav"
    return "webm"


def transcribe(audio: bytes, filename: str = "chunk.webm", mime: str | None = None,
               language_code: str = "unknown") -> dict:
    """Noisy, code-mixed speakerphone audio -> text + detected language.
    saaras:v4 biased with scam keyterms; falls back to saaras:v3 codemix if v4 errors."""
    codec = _codec(filename, mime)
    ctype = {"mp4": "audio/mp4", "ogg": "audio/ogg", "wav": "audio/wav"}.get(codec, "audio/webm")

    def v4():
        return _sdk().speech_to_text.transcribe(
            file=(filename, audio, ctype), model=config.STT_MODEL, language_code=language_code,
            input_audio_codec=codec, keyterms=config.STT_KEYTERMS)

    def v3():
        return _sdk().speech_to_text.transcribe(
            file=(filename, audio, ctype), model=config.STT_FALLBACK_MODEL, mode="codemix",
            language_code=language_code)

    model = config.STT_MODEL
    try:
        res, ms = _timed("saaras", v4, retries=0)
    except SarvamError:
        model = config.STT_FALLBACK_MODEL
        res, ms = _timed("saaras(v3-fallback)", v3)
    return {
        "transcript": (res.transcript or "").strip(),
        "language_code": res.language_code,
        "language_probability": res.language_probability,
        "model": model,
        "ms": ms,
    }


# ---------------------------------------------------------------- Sarvam-105B
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    text = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _chat_raw(messages: list[dict], max_tokens: int, response_format: dict, temperature: float) -> str:
    body = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # None = thinking off. Measured: ~0.5 s vs ~6.5 s with "low"; essential for a live call.
        "reasoning_effort": config.LLM_REASONING_EFFORT,
        "response_format": response_format,
    }
    r = httpx.post(
        f"{config.SARVAM_BASE_URL}/v1/chat/completions",
        headers={"api-subscription-key": config.get_api_key()},
        json=body,
        timeout=config.HTTP_TIMEOUT,
    )
    if r.status_code >= 400:
        raise SarvamError(f"HTTP {r.status_code}: {r.text[:300]}")
    content = r.json()["choices"][0]["message"].get("content")
    if not content:
        raise SarvamError("empty content (max_tokens too small?)")
    return content


def chat_json(system: str, user: str, max_tokens: int = 1200, schema: dict | None = None,
              temperature: float = 0.2, name: str = "sarvam-105b") -> tuple[dict, int]:
    """Strict-JSON call. Fallback chain: json_schema -> json_object -> 'Return valid JSON only.' retry."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    formats = [{"type": "json_object"}]
    if schema:
        formats.insert(0, {"type": "json_schema", "json_schema": {"name": "kavach", "strict": True, "schema": schema}})
    total, text = 0, ""
    for fmt in formats:
        try:
            text, ms = _timed(f"{name}[{fmt['type']}]", lambda fmt=fmt: _chat_raw(messages, max_tokens, fmt, temperature), retries=0)
            total += ms
            return _extract_json(text), total
        except (SarvamError, json.JSONDecodeError, ValueError):
            continue
    messages += [{"role": "assistant", "content": text or "{}"}, {"role": "user", "content": "Return valid JSON only."}]
    text, ms = _timed(f"{name}(json-retry)", lambda: _chat_raw(messages, max_tokens * 2, {"type": "json_object"}, temperature))
    try:
        return _extract_json(text), total + ms
    except (json.JSONDecodeError, ValueError) as e:
        raise SarvamError(f"sarvam-105b returned non-JSON: {text[:200]}") from e


# ---------------------------------------------------------------- Bulbul
def speak(text: str, language_code: str, speaker: str | None = None, pace: float = config.WARN_PACE) -> tuple[str, int]:
    """Returns base64 WAV."""
    if language_code not in config.TTS_LANGS:
        language_code = "hi-IN"
    speaker = speaker or config.LANGUAGES.get(language_code, {}).get("speaker", "shubh")

    def call():
        return _sdk().text_to_speech.convert(
            text=text[:2400],
            language_code=language_code,
            speaker=speaker,
            model=config.TTS_MODEL,
            pace=pace,
            speech_sample_rate=24000,
        )

    res, ms = _timed("bulbul", call)
    return "".join(res.audios), ms


# ---------------------------------------------------------------- Sarvam Vision
_TERMINAL = {"completed", "partially_completed", "failed", "rejected"}


def _mime(filename: str) -> str:
    n = filename.lower()
    return "image/png" if n.endswith(".png") else "application/pdf" if n.endswith(".pdf") else "image/jpeg"


def _wait(job_id: str, timeout_s: float) -> str:
    sdk, t0, status = _sdk(), time.perf_counter(), "pending"
    while True:
        status = sdk.doc_ai.get_status(job_id=job_id).status.lower()
        if status in _TERMINAL:
            break
        if time.perf_counter() - t0 > timeout_s:
            raise SarvamError("Sarvam Vision timed out")
        time.sleep(1.5)
    if status in {"failed", "rejected"}:
        raise SarvamError(f"Sarvam Vision job {status}")
    return status


def read_document(image: bytes, filename: str, language: str | None = None, timeout_s: float = 60) -> tuple[str, int]:
    """Doc AI digitise job: submit -> poll -> full OCR text in reading order."""
    sdk, t0 = _sdk(), time.perf_counter()
    job, _ = _timed("vision.digitise", lambda: sdk.doc_ai.digitise(
        file=[(filename, image, _mime(filename))], language=language or config.VISION_LANGUAGE, content_type="mixed"))
    _wait(job.job_id, timeout_s)
    # Digitise jobs produce html by default, so "md" is refused; JSON blocks give clean reading-order text.
    res = sdk.doc_ai.get_results(job_id=job.job_id, format="json")
    pages = []
    for doc in res.documents or []:
        for page in doc.pages or []:
            blocks = getattr(page, "blocks", None) or []
            if blocks:
                blocks = sorted(blocks, key=lambda b: b.get("reading_order", 0))
                pages.append("\n".join(b.get("text", "") for b in blocks if b.get("text")))
            elif page.content:
                pages.append(page.content)
    return "\n\n".join(pages).strip(), int((time.perf_counter() - t0) * 1000)


def extract_fields(image: bytes, filename: str, schema: dict, timeout_s: float = 60) -> tuple[dict, dict, int]:
    """Doc AI extract job -> (fields, per-field confidence, ms)."""
    sdk, t0 = _sdk(), time.perf_counter()
    job, _ = _timed("vision.extract", lambda: sdk.doc_ai.extract(
        file=[(filename, image, _mime(filename))], schema=json.dumps(schema), output_format="json"))
    _wait(job.job_id, timeout_s)
    res = sdk.doc_ai.get_results(job_id=job.job_id)
    fields = getattr(res, "result", None) or {}
    ann = getattr(res, "annotations", None) or {}
    conf = {k: (v.get("confidence") if isinstance(v, dict) else None) for k, v in ann.items()}
    return fields, conf, int((time.perf_counter() - t0) * 1000)
