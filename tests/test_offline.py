"""Offline unit tests (no network, no API key). Run: pytest -q"""
import os

os.environ.setdefault("SARVAM_API_KEY", "")

from app import dadi, evidence, risk  # noqa: E402


def test_instant_score_flags_scam_combo():
    r = risk.instant_score("Main CBI se bol raha hoon, kisi ko mat batana, paise safe account mein transfer karo")
    keys = {f["flag"] for f in r["flags"]}
    assert {"authority_impersonation", "secrecy_demand", "money_or_otp_request"} <= keys
    assert 50 <= r["score"] <= risk.INSTANT_CAP


def test_instant_score_never_reaches_alarm_alone():
    r = risk.instant_score("CBI police arrest warrant drugs parcel kisi ko mat batana video call transfer OTP AnyDesk")
    assert r["score"] < 70  # only the AI can trigger the takeover


def test_customer_care_is_not_customs():
    assert risk.instant_score("कार्ड के पीछे वाले कस्टमर केयर नंबर पर कॉल")["score"] == 0


def test_verify_evidence_drops_invented_quotes():
    flags = [{"evidence_quote": "kisi ko mat batana"}, {"evidence_quote": "this was never said"}]
    kept = risk.verify_evidence(flags, "Ye case confidential hai, kisi ko mat batana.")
    assert len(kept) == 1


def test_regex_evidence_upi_phone_ifsc_account():
    items = evidence.regex_extract("UPI cbi.verify@okaxis, number 9876543210, A/C 50100234567812 IFSC HDFC0001234")
    got = {(i["kind"], i["value"]) for i in items}
    assert ("upi_id", "cbi.verify@okaxis") in got
    assert ("phone_number", "9876543210") in got
    assert ("ifsc", "HDFC0001234") in got
    assert ("bank_account", "50100234567812") in got


def test_spoken_numbers_and_dot_at():
    items = evidence.regex_extract("UPI hai cbi dot verify at okaxis, nine eight seven six five four three two one zero")
    got = {(i["kind"], i["value"]) for i in items}
    assert ("upi_id", "cbi.verify@okaxis") in got and ("phone_number", "9876543210") in got


def test_locker_dedupes():
    lk = evidence.Locker()
    assert len(lk.add([{"kind": "upi_id", "value": "a.b@okaxis"}], 1)) == 1
    assert len(lk.add([{"kind": "other", "value": "a.b@okaxis"}], 2)) == 0


def test_dadi_never_speaks_long_numbers():
    assert "987" not in dadi.sanitise("Beta mera number 9876543210 hai")
    assert "४५६" not in dadi.sanitise("खाता ४५६७८ है")
    assert dadi.sanitise("Ek minute beta, 4… 7… chashma kahan gaya") == "Ek minute beta, 4… 7… chashma kahan gaya"
