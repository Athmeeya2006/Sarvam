"""Instant keyword pre-scorer (no network, under 1 ms) + merge with the Sarvam-105B score.

The instant score makes the gauge react while the LLM is thinking. It is labelled "instant check" in the UI and
is always replaced by the AI score once that arrives (the AI is the source of truth).
"""
from __future__ import annotations

import re

# flag -> (weight, patterns). Patterns cover Devanagari, romanised Hinglish, English and some Tamil.
KEYWORDS: dict[str, tuple[int, list[str]]] = {
    "authority_impersonation": (22, [
        r"\bcbi\b", r"सीबीआई", r"\bpolice\b", r"पुलिस", r"\bcustoms?\b(?! ?care)", r"कस्टम(?!र)", r"narcotics",
        r"नारकोटिक्स", r"\bncb\b", r"enforcement directorate", r"\brbi\b", r"आरबीआई", r"\btrai\b", r"ट्राई",
        r"inspector", r"इंस्पेक्टर", r"officer", r"ऑफिसर", r"अधिकारी", r"\bfedex\b", r"फेडेक्स", r"courier",
        r"कूरियर", r"crime branch", r"cyber cell", r"साइबर", r"காவல்", r"போலீஸ்", r"சிபிஐ", r"அதிகாரி",
    ]),
    "urgency_threat": (18, [
        r"arrest", r"अरेस्ट", r"गिरफ्तार", r"giraftar", r"warrant", r"वारंट", r"\bfir\b", r"एफआईआर", r"drugs?",
        r"ड्रग्स?", r"parcel", r"पार्सल", r"money laundering", r"मनी लॉन्ड्रिंग", r"account (freeze|block|band)",
        r"खाता (बंद|फ्रीज)", r"sim (block|band)", r"सिम (बंद|ब्लॉक)", r"jail", r"जेल",
        r"(\d+|do|दो) (ghante|घंटे)", r"turant", r"तुरंत", r"கைது", r"போதை", r"வாரண்ட்", r"சிறை",
    ]),
    "secrecy_demand": (20, [
        r"kisi ko (mat|na|nahi) bata", r"किसी को (मत|ना|न|नहीं) बता", r"confidential", r"गोपनीय", r"secret",
        r"सीक्रेट", r"family ko (mat|na)", r"परिवार को (मत|न)", r"யாரிடமும் (சொல்ல|கூற)", r"ரகசிய",
    ]),
    "stay_on_call_or_video": (18, [
        r"digital arrest", r"डिजिटल अरेस्ट", r"video call", r"वीडियो कॉल", r"call (mat|na) kaat", r"कॉल (मत|न) काट",
        r"phone (mat|na) kaat", r"फ़?फोन (मत|न) काट", r"disconnect", r"band kamre", r"बंद कमरे", r"டிஜிட்டல் அரெஸ்ட்",
        r"வீடியோ கால்",
    ]),
    "money_or_otp_request": (24, [
        r"transfer", r"ट्रांसफर", r"paise? (bhej|daal|jama)", r"पैसे? (भेज|डाल|जमा)", r"\bupi\b", r"यूपीआई",
        r"safe (custody )?account", r"सेफ अकाउंट", r"जुर्माना", r"penalty", r"deposit", r"lakh", r"लाख",
        r"\botp\b", r"ओटीपी", r"\bcvv\b", r"card number", r"कार्ड नंबर", r"verification", r"वेरिफिकेशन",
        r"सत्यापन", r"பணம்", r"அனுப்ப", r"ஓடிபி",
    ]),
    "remote_app_install": (22, [r"anydesk", r"teamviewer", r"screen share", r"app (download|install)", r"एनीडेस्क"]),
    "personal_data_request": (12, [r"aadhaar number", r"आधार नंबर", r"pan (card|number)", r"पैन", r"date of birth"]),
    "too_good_offer": (14, [r"lottery", r"लॉटरी", r"\bkbc\b", r"prize", r"इनाम", r"cashback", r"jeet", r"जीत"]),
}

LABELS = {
    "authority_impersonation": ("Pretending to be police / CBI / officials", "अधिकारी होने का दावा"),
    "urgency_threat": ("Threat or urgency", "धमकी / जल्दबाज़ी"),
    "secrecy_demand": ("Asks you to keep it secret", "गुप्त रखने को कहा"),
    "stay_on_call_or_video": ("Keeps you on call / 'digital arrest'", "कॉल पर रोके रखना"),
    "money_or_otp_request": ("Asks for money / OTP", "पैसे या OTP की माँग"),
    "remote_app_install": ("Asks to install a remote app", "रिमोट ऐप डलवाना"),
    "personal_data_request": ("Asks for personal data", "निजी जानकारी की माँग"),
    "too_good_offer": ("Too-good-to-be-true offer", "लालच वाला ऑफ़र"),
}
FLAG_KEYS = list(LABELS)

INSTANT_CAP = 65

_COMPILED = {k: (w, [re.compile(p, re.IGNORECASE) for p in pats]) for k, (w, pats) in KEYWORDS.items()}


def instant_score(text: str) -> dict:
    """Return {score, flags:[{flag,label_en,label_local,evidence_quote}]} using keyword hits."""
    flags, score = [], 0
    for key, (weight, pats) in _COMPILED.items():
        for p in pats:
            m = p.search(text)
            if m:
                s, e = max(0, m.start() - 25), min(len(text), m.end() + 25)
                en, local = LABELS[key]
                flags.append({"flag": key, "label_en": en, "label_local": local,
                              "evidence_quote": text[s:e].strip(), "source": "instant"})
                score += weight
                break
    # Combinations are what make a scam: authority + (secrecy|money|otp) is a strong signal.
    keys = {f["flag"] for f in flags}
    if "authority_impersonation" in keys and keys & {"secrecy_demand", "money_or_otp_request", "remote_app_install", "stay_on_call_or_video"}:
        score += 10
    if len(keys) <= 1:
        score = min(score, 30)  # a single keyword ("police", "case") is not enough on its own
    # The instant check is only a hint: it is capped below the alarm threshold, so ONLY the AI can trigger a takeover.
    return {"score": min(score, INSTANT_CAP), "flags": flags}


def normalise_ai(result: dict) -> dict:
    """Defensive cleanup of LLM JSON so the UI never breaks."""
    try:
        score = int(float(result.get("risk_score", 0)))
    except (TypeError, ValueError):
        score = 0
    flags = []
    for f in result.get("red_flags") or []:
        if isinstance(f, dict) and f.get("evidence_quote"):
            key = f.get("flag", "other")
            en, local = LABELS.get(key, (key.replace("_", " ").title(), ""))
            flags.append({
                "flag": key,
                "label_en": f.get("label_en") or en,
                "label_local": f.get("label_local") or local,
                "evidence_quote": str(f["evidence_quote"])[:240],
                "source": "ai",
            })
    return {
        "risk_score": max(0, min(100, score)),
        "scam_type": result.get("scam_type") or "none",
        "red_flags": flags,
        "advice": result.get("advice") or "",
        "warning_text": result.get("warning_text") or "",
    }


def verify_evidence(flags: list[dict], transcript: str) -> list[dict]:
    """Explainability guard: keep only flags whose quote really appears (loosely) in the transcript."""
    norm = lambda s: re.sub(r"[\s।,.!?\"'’]+", " ", s.lower()).strip()  # noqa: E731
    t = norm(transcript)
    kept = []
    for f in flags:
        q = norm(f["evidence_quote"])
        words = q.split()
        if q and (q in t or (len(words) >= 3 and sum(w in t for w in words) / len(words) >= 0.7)):
            kept.append(f)
    return kept
