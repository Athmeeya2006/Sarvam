"""Session store.

- Local / single server: in-memory (default).
- Serverless (Vercel): Upstash Redis over its REST API, so every function instance sees the same session.
  Enabled automatically when UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN (or Vercel's KV_REST_API_URL +
  KV_REST_API_TOKEN) are set.

Sessions are stored as a hash of JSON fields, and each request writes back ONLY the fields it changed, so two
requests on the same call (e.g. a chunk and a warning) never overwrite each other's data."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

import httpx

SESSION_TTL_S = 60 * 60


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._exp: dict[str, float] = {}
        self._locks: dict[str, tuple[str, float]] = {}
        self._mu = threading.Lock()

    def _gc(self):
        now = time.time()
        for k in [k for k, t in self._exp.items() if t < now]:
            self._data.pop(k, None)
            self._exp.pop(k, None)

    def get(self, sid: str) -> dict | None:
        with self._mu:
            self._gc()
            d = self._data.get(sid)
            return json.loads(json.dumps(d)) if d is not None else None  # copy, like Redis would

    def update(self, sid: str, fields: dict) -> None:
        with self._mu:
            self._data.setdefault(sid, {}).update(json.loads(json.dumps(fields)))
            self._exp[sid] = time.time() + SESSION_TTL_S

    def delete(self, sid: str) -> None:
        with self._mu:
            self._data.pop(sid, None)
            self._exp.pop(sid, None)

    def lock(self, name: str, ttl_s: float) -> str | None:
        with self._mu:
            now = time.time()
            held = self._locks.get(name)
            if held and held[1] > now:
                return None
            token = uuid.uuid4().hex
            self._locks[name] = (token, now + ttl_s)
            return token

    def unlock(self, name: str, token: str) -> None:
        with self._mu:
            if self._locks.get(name, ("",))[0] == token:
                self._locks.pop(name, None)


class UpstashStore:
    kind = "upstash-redis"

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.client = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=10)

    def _pipe(self, *commands: list) -> list:
        r = self.client.post(f"{self.url}/pipeline", json=[[str(x) for x in c] for c in commands])
        r.raise_for_status()
        out = r.json()
        for item in out:
            if "error" in item:
                raise RuntimeError(f"Redis error: {item['error']}")
        return [item.get("result") for item in out]

    @staticmethod
    def _key(sid: str) -> str:
        return f"kavach:s:{sid}"

    def get(self, sid: str) -> dict | None:
        (flat,) = self._pipe(["HGETALL", self._key(sid)])
        if not flat:
            return None
        return {flat[i]: json.loads(flat[i + 1]) for i in range(0, len(flat), 2)}

    def update(self, sid: str, fields: dict) -> None:
        if not fields:
            return
        hset = ["HSET", self._key(sid)]
        for k, v in fields.items():
            hset += [k, json.dumps(v, ensure_ascii=False)]
        self._pipe(hset, ["EXPIRE", self._key(sid), SESSION_TTL_S])

    def delete(self, sid: str) -> None:
        self._pipe(["DEL", self._key(sid)])

    def lock(self, name: str, ttl_s: float) -> str | None:
        token = uuid.uuid4().hex
        (ok,) = self._pipe(["SET", f"kavach:lock:{name}", token, "NX", "PX", int(ttl_s * 1000)])
        return token if ok == "OK" else None

    def unlock(self, name: str, token: str) -> None:
        # Only release a lock we still own (it may have expired and been taken by another request).
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        self._pipe(["EVAL", script, 1, f"kavach:lock:{name}", token])


def make_store():
    url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN")
    if url and token:
        return UpstashStore(url, token)
    return MemoryStore()
