# Sarvam API notes (verified 2026-10-17 against docs.sarvam.ai + `sarvamai` SDK 0.1.37)

| Item | Value |
|---|---|
| Base URL | `https://api.sarvam.ai` |
| Auth header | `api-subscription-key: <key>` (chat also accepts `Authorization: Bearer`) |
| Python SDK | `pip install sarvamai` → `SarvamAI(api_subscription_key=...)` |

## Speech-to-Text: Saaras
- `client.speech_to_text.transcribe(file=, model=, mode=, language_code=)` → `POST /speech-to-text`
- Models: `saaras:v3` (API default, supports `mode`), `saaras:v4` (latest), `saaras:v3-realtime` (WebSocket)
- Modes: `transcribe`, `translate`, `verbatim`, `translit`, **`codemix`** (we use this: "मैं CBI से बोल रहा हूँ")
- `language_code="unknown"` → auto-detect; response has `transcript`, `language_code`, `language_probability`
- **Sync REST max 30 s audio.** Formats: MP3, WAV, AAC, AIFF, OGG/Opus, FLAC, MP4/M4A, AMR, WMA, **WebM**, PCM (16 kHz)
  → browser `MediaRecorder` webm/opus chunks go straight in, so **no ffmpeg needed**.
- Streaming (WebSocket) accepts only WAV/PCM 16k/8k. We use chunked REST (simpler and robust for the demo).

## Text-to-Speech: Bulbul
- `client.text_to_speech.convert(text=, language_code=, speaker=, model=, pace=, speech_sample_rate=)` → `POST /text-to-speech`
- Models: `bulbul:v3` (API default, 30+ voices), `bulbul:v4-flash` (200+ persona voices), `bulbul:v2` (legacy)
- v3 speakers (lowercase): shubh (default), aditya, ritu, priya, neha, rahul, pooja, rohan, simran, kavya, amit, dev, ishita, shreya, ratan, varun, manan, sumit, roopa, kabir, aayan, ashutosh, advait, anand, tanya, tarun, sunny, mani, gokul, vijay, shruti, suhani, mohit, kavitha, rehan, soham, rupali
- **Max 2500 chars** (REST). v3 has no pitch/loudness; pace is 0.5 to 2.0. Response `audios: [base64 WAV]`.
- Language codes: `hi-IN, bn-IN, ta-IN, te-IN, kn-IN, ml-IN, mr-IN, gu-IN, pa-IN, od-IN, en-IN`

## Chat: Sarvam-105B
- `POST /v1/chat/completions` (OpenAI-style). Models: `sarvam-105b` (128K), `sarvam-105b-conversations` (32K)
- `sarvam-m` is deprecated.
- Thinking is **on by default (low)**; it eats `max_tokens`. We send `"reasoning_effort": null` → ~0.4 s replies.
- `response_format: {"type": "json_object"}` is supported. We still strip fences and retry once with "Return valid JSON only."
- We call it via raw `httpx` because the SDK drops `reasoning_effort=None` (it treats None as omitted).

## Vision: Sarvam Vision (Document AI)
- **Job-based**: `client.doc_ai.digitise(file=[(name, bytes, mime)], language="hi-IN")` → `job_id` (creates and starts in one call)
- Poll `client.doc_ai.get_status(job_id=)` until `completed | partially_completed | failed | rejected` (~2 to 3 s for one page)
- `client.doc_ai.get_results(job_id=, format="json")` → `documents[].pages[].blocks[{text, layout_tag, reading_order}]`
  - ⚠️ `format="md"` returns 400 "job produced html, cannot serve md". Use json (or html).
- Inputs: PDF (≤10 pages), PNG, JPEG, ZIP of images; 22 Indic languages plus English.

## Smoke test (`python scripts/smoke_test.py`) on the first run
| Model | Latency |
|---|---|
| Bulbul v3 (hi-IN) | ~1.6 s |
| Saaras v3 codemix | ~0.7 to 0.9 s |
| Sarvam-105B (no reasoning) | ~0.4 s |
| Sarvam Vision (1 page) | ~2.4 to 3 s |
