"""Evidence Locker: regex (instant, exact) + Sarvam-105B extraction (claimed name, badge, case number, amount)."""
from __future__ import annotations

import re
import time

UPI_RE = re.compile(r"\b[\w.\-]{2,}@[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\-\s]?)?[6-9]\d{9}(?!\d)")
ACCOUNT_RE = re.compile(r"(?<!\d)\d{9,18}(?!\d)")
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
EMAIL_DOMAINS = {"gmail", "yahoo", "outlook", "hotmail", "rediffmail"}

# Spoken digits that Saaras may emit as words.
_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "छः": "6", "सात": "7", "आठ": "8", "नौ": "9",
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalise_spoken(text: str) -> str:
    """'nine eight seven six…' / Devanagari numerals -> digits; join digit groups split by spaces/dashes."""
    t = text.translate(_DEVANAGARI_DIGITS)
    t = re.sub(r"(?<=\d)[\s\-](?=\d)", "", t)
    words = re.split(r"(\s+)", t)
    out = []
    for w in words:
        out.append(_DIGIT_WORDS.get(w.lower().strip(",.।"), w))
    t = "".join(out)
    t = re.sub(r"(?<=\d)\s+(?=\d)", "", t)
    # "cbi dot verify at okaxis" -> "cbi.verify@okaxis"
    t = re.sub(r"\s+(?:dot|डॉट)\s+", ".", t, flags=re.I)
    t = re.sub(r"\s+(?:at the rate|at|एट)\s+(?=[a-zA-Z]{2,}\b)", "@", t, flags=re.I)
    return t


def _quote(text: str, start: int, end: int) -> str:
    s, e = max(0, start - 30), min(len(text), end + 30)
    return text[s:e].strip()


def regex_extract(text: str) -> list[dict]:
    items: list[dict] = []
    norm = normalise_spoken(text)
    for m in UPI_RE.finditer(norm):
        handle = m.group(0).split("@")[1].lower()
        kind = "email" if handle in EMAIL_DOMAINS or "." in handle else "upi_id"
        items.append({"kind": kind, "value": m.group(0), "quote": _quote(norm, m.start(), m.end())})
    phones = set()
    for m in PHONE_RE.finditer(norm):
        num = re.sub(r"\D", "", m.group(0))[-10:]
        phones.add(num)
        items.append({"kind": "phone_number", "value": num, "quote": _quote(norm, m.start(), m.end())})
    for m in IFSC_RE.finditer(norm.upper()):
        items.append({"kind": "ifsc", "value": m.group(0), "quote": _quote(norm, m.start(), m.end())})
    for m in ACCOUNT_RE.finditer(norm):
        if m.group(0)[-10:] in phones and len(m.group(0)) <= 12:
            continue
        items.append({"kind": "bank_account", "value": m.group(0), "quote": _quote(norm, m.start(), m.end())})
    for i in items:
        i["source"] = "regex"
    return items


class Locker:
    """De-duplicated evidence items with timestamps."""

    def __init__(self):
        self.items: list[dict] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, items: list[dict], t: float) -> list[dict]:
        new = []
        for it in items:
            value = str(it.get("value", "")).strip()
            kind = it.get("kind", "other")
            if not value or value.lower() in {"none", "null", "not captured", "unknown"}:
                continue
            key = (kind, re.sub(r"[\s\-]", "", value.lower()))
            # a UPI id the LLM also found as "other" etc. -> dedupe on value alone for exact identifiers
            if key in self._seen or any(k[1] == key[1] for k in self._seen):
                continue
            self._seen.add(key)
            item = {"kind": kind, "value": value, "quote": str(it.get("quote", ""))[:200],
                    "source": it.get("source", "ai"), "t": round(t, 1), "at": time.strftime("%H:%M:%S")}
            self.items.append(item)
            new.append(item)
        return new

    def has(self, kind: str) -> bool:
        return any(i["kind"] == kind for i in self.items)

    def collected_kinds(self) -> list[str]:
        mapping = {"upi_id": "upi_id", "claimed_name": "name_badge", "badge_number": "name_badge",
                   "phone_number": "callback_number", "bank_account": "account_ifsc", "ifsc": "account_ifsc",
                   "case_number": "case_number"}
        return sorted({mapping[i["kind"]] for i in self.items if i["kind"] in mapping})
