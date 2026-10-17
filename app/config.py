"""Configuration. The API key is read ONLY from the environment / .env and is never logged."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SARVAM_BASE_URL = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
STT_MODEL = os.getenv("KAVACH_STT_MODEL", "saaras:v4")
STT_FALLBACK_MODEL = "saaras:v3"  # used with mode="codemix" if v4 errors
# Biases Saaras v4 toward the words scammers actually use (v4 only, max 50).
STT_KEYTERMS = ["CBI", "digital arrest", "Aadhaar", "money laundering", "TRAI", "customs", "FedEx", "RBI", "UPI",
                "OTP", "video call", "arrest warrant", "ED", "narcotics", "IFSC", "account number", "NCB", "FIR",
                "safe account", "verification", "AnyDesk", "KYC", "badge number", "case number"]
TTS_MODEL = os.getenv("KAVACH_TTS_MODEL", "bulbul:v3")
LLM_MODEL = os.getenv("KAVACH_LLM_MODEL", "sarvam-105b")
# None disables thinking: measured ~0.5 s vs ~6.5 s with "low". Set KAVACH_REASONING=low to turn it on.
LLM_REASONING_EFFORT = os.getenv("KAVACH_REASONING") or None
VISION_LANGUAGE = os.getenv("KAVACH_VISION_LANG", "hi-IN")

RISK_THRESHOLD = int(os.getenv("KAVACH_RISK_THRESHOLD", "70"))
TRANSCRIPT_WINDOW_CHARS = 1500  # ~60 s of speech sent to the LLM
CHUNK_SECONDS = 6  # browser recorder chunk length (sent to the frontend)
WARN_PACE = 1.0

# Dadi Mode voice (Bulbul v3, female, slowed down). Change per language here.
DADI_PACE = float(os.getenv("KAVACH_DADI_PACE", "0.8"))
DADI_VOICES = {"hi-IN": "roopa", "ta-IN": "kavitha", "default": "roopa"}
# Bulbul v3 TTS languages; anything else falls back to hi-IN.
TTS_LANGS = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}
# Real data Dadi must NEVER say (e.g. the user's own phone/account digits). Comma-separated in .env.
REAL_DATA_BLOCKLIST = [x.strip() for x in os.getenv("KAVACH_REAL_DATA_BLOCKLIST", "").split(",") if x.strip()]
HTTP_TIMEOUT = 45.0

# Language → Bulbul v3 voice. Calm, mature voices chosen per language.
LANGUAGES = {
    "hi-IN": {"name": "हिन्दी", "english": "Hindi", "speaker": "shubh"},
    "ta-IN": {"name": "தமிழ்", "english": "Tamil", "speaker": "kavitha"},
    "bn-IN": {"name": "বাংলা", "english": "Bengali", "speaker": "shubh"},
    "mr-IN": {"name": "मराठी", "english": "Marathi", "speaker": "shubh"},
    "te-IN": {"name": "తెలుగు", "english": "Telugu", "speaker": "shubh"},
    "kn-IN": {"name": "ಕನ್ನಡ", "english": "Kannada", "speaker": "shubh"},
    "en-IN": {"name": "English", "english": "English", "speaker": "shubh"},
}


def get_api_key(required: bool = True) -> str:
    key = os.getenv("SARVAM_API_KEY", "").strip()
    if not key and required and os.getenv("VERCEL"):
        # Serverless: don't kill the function; surface a clear error to the caller instead.
        raise RuntimeError("SARVAM_API_KEY is not set. Add it in Vercel → Settings → Environment Variables and redeploy.")
    if not key and required:
        sys.stderr.write(
            "\n[kavach] SARVAM_API_KEY is not set.\n"
            "  cp .env.example .env   # then paste your key from https://dashboard.sarvam.ai\n\n"
        )
        raise SystemExit(1)
    return key
