"""Prove a call survives being served by DIFFERENT server instances (what happens on Vercel).

Start 2+ independent servers that share one Upstash/Redis store, then:
    python scripts/cross_instance_test.py http://host-a:8000 http://host-b:8001 [...]
Each step of one call is sent to the next instance in turn (text mode, so it is quick)."""
import itertools
import sys

import httpx

bases = sys.argv[1:] or ["http://127.0.0.1:8001", "http://127.0.0.1:8002"]
inst = itertools.cycle([httpx.Client(base_url=b, timeout=90) for b in bases])
ok = True


def step(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {name}  {detail}")


c = next(inst)
sid = c.post("/api/session/start", json={"user_lang": "hi-IN"}).json()["session_id"]
step("session started", sid, f"on {c.base_url}")
lines = ["Main CBI cyber cell se Inspector Vikram bol raha hoon, aapke Aadhaar par drugs ka parcel mila hai.",
         "Ye case confidential hai, kisi ko mat batana, video call par rahiye, aap digital arrest mein hain.",
         "RBI verification ke liye saare paise safe account mein transfer kariye."]
for line in lines:
    c = next(inst)
    r = c.post("/api/demo/text", json={"session_id": sid, "text": line})
    d = r.json()
    step(f"chunk on {c.base_url}", r.status_code == 200 and line in d.get("full_transcript", ""),
         f"chunks so far={d.get('full_transcript', '').count('.')} risk={(d.get('ai_result') or {}).get('risk_score')}")
step("transcript kept every line across instances", all(x in d["full_transcript"] for x in lines))
step("AI risk ≥ 70", (d.get("ai_result") or {}).get("risk_score", 0) >= 70)
c = next(inst)
step(f"warn on {c.base_url}", c.post(f"/api/session/{sid}/warn", json={}).status_code == 200)
c = next(inst)
step(f"cooldown enforced on another instance ({c.base_url})", c.post(f"/api/session/{sid}/warn", json={}).status_code == 429)
c = next(inst)
step(f"dadi/start on {c.base_url}", c.post(f"/api/session/{sid}/dadi/start").status_code == 200)
c = next(inst)
t = c.post("/api/demo/text", json={"session_id": sid, "text": "Mera UPI hai cbi.verify@okaxis", "dadi": True}).json()
c = next(inst)
t2 = c.post("/api/demo/text", json={"session_id": sid, "text": "Aur number 9876543210 hai", "dadi": True}).json()
vals = {i["value"] for i in t2.get("evidence_locker", [])}
step("evidence from both instances in one locker", {"cbi.verify@okaxis", "9876543210"} <= vals, ", ".join(sorted(vals)))
step("Dadi history continued across instances", len(t2.get("dadi_text", "")) > 0)
c = next(inst)
e = c.post(f"/api/session/{sid}/end").json()
step(f"end on {c.base_url}: complaint has UPI", "cbi.verify@okaxis" in e.get("complaint_en", ""))
step("session gone everywhere after end", all(
    httpx.post(f"{b}/api/session/{sid}/end", timeout=30).status_code == 404 for b in bases))
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
