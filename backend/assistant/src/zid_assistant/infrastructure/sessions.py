"""Ephemeral anonymous guest sessions (ADR-07, RET-01/02). No account, no tenant, no stable identifier, no transcript.

The cookie carries an opaque random handle. The server stores state under sha256(handle), so a leaked store cannot be used to
take over a session. What is kept: expiry times, locale, a small idempotency cache of already-validated response packets
(needed to replay a duplicate send), and the ids of responses issued (to accept feedback only for real responses). Visitor text is
never stored; the cached packet is the server's own answer.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import valkey.asyncio as valkey_asyncio


def _key(handle: str) -> str:
    return hashlib.sha256(handle.encode()).hexdigest()


@dataclass(slots=True)
class SessionData:
    created: float
    last_active: float
    absolute_expiry: float
    locale: str


class SessionStore(Protocol):
    async def create(self, locale: str) -> tuple[str, SessionData]: ...
    async def get(self, handle: str) -> SessionData | None: ...
    async def touch(self, handle: str) -> SessionData | None: ...
    async def delete(self, handle: str) -> None: ...
    async def put_message(
        self, handle: str, message_id: str, digest: str, packet_json: str
    ) -> None: ...
    async def get_message(self, handle: str, message_id: str) -> tuple[str, str] | None: ...
    async def add_response(self, handle: str, response_id: str) -> None: ...
    async def has_response(self, handle: str, response_id: str) -> bool: ...
    async def mark_feedback(self, handle: str, response_id: str) -> bool: ...


class MemorySessionStore:
    """Single-process store for local, tests and one replica. Production with >1 replica uses Valkey."""

    def __init__(self, idle: int, absolute: int, clock: Callable[[], float] = time.time) -> None:
        self._idle, self._abs, self._clock = idle, absolute, clock
        self._s: dict[str, SessionData] = {}
        self._m: dict[tuple[str, str], tuple[str, str]] = {}
        self._r: dict[str, set[str]] = {}
        self._fb: dict[str, set[str]] = {}

    def _live(self, k: str) -> SessionData | None:
        d = self._s.get(k)
        if d is None:
            return None
        now = self._clock()
        if now >= d.absolute_expiry or now - d.last_active >= self._idle:
            self._drop(k)
            return None
        return d

    def _drop(self, k: str) -> None:
        self._s.pop(k, None)
        self._r.pop(k, None)
        self._fb.pop(k, None)
        for mk in [m for m in self._m if m[0] == k]:
            self._m.pop(mk, None)

    async def create(self, locale: str) -> tuple[str, SessionData]:
        handle = secrets.token_urlsafe(32)
        now = self._clock()
        d = SessionData(now, now, now + self._abs, locale)
        self._s[_key(handle)] = d
        return handle, d

    async def get(self, handle: str) -> SessionData | None:
        return self._live(_key(handle))

    async def touch(self, handle: str) -> SessionData | None:
        d = self._live(_key(handle))
        if d is not None:
            d.last_active = self._clock()
        return d

    async def delete(self, handle: str) -> None:
        self._drop(_key(handle))

    async def put_message(
        self, handle: str, message_id: str, digest: str, packet_json: str
    ) -> None:
        self._m[(_key(handle), message_id)] = (digest, packet_json)

    async def get_message(self, handle: str, message_id: str) -> tuple[str, str] | None:
        return self._m.get((_key(handle), message_id))

    async def add_response(self, handle: str, response_id: str) -> None:
        self._r.setdefault(_key(handle), set()).add(response_id)

    async def has_response(self, handle: str, response_id: str) -> bool:
        return response_id in self._r.get(_key(handle), set())

    async def mark_feedback(self, handle: str, response_id: str) -> bool:
        """True the first time feedback is recorded for a response (one vote per response)."""
        seen = self._fb.setdefault(_key(handle), set())
        if response_id in seen:
            return False
        seen.add(response_id)
        return True


class ValkeySessionStore:
    """Shared store for several replicas. Every key expires on its own; nothing outlives the absolute expiry."""

    def __init__(
        self, url: str, idle: int, absolute: int, clock: Callable[[], float] = time.time
    ) -> None:
        self._r = valkey_asyncio.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]
        self._idle, self._abs, self._clock = idle, absolute, clock

    def _k(self, handle: str, part: str = "s") -> str:
        return f"zpa:{part}:{_key(handle)}"

    async def create(self, locale: str) -> tuple[str, SessionData]:
        handle = secrets.token_urlsafe(32)
        now = self._clock()
        d = SessionData(now, now, now + self._abs, locale)
        await self._r.set(
            self._k(handle),
            json.dumps([d.created, d.last_active, d.absolute_expiry, d.locale]),
            ex=min(self._idle, self._abs),
        )
        return handle, d

    async def get(self, handle: str) -> SessionData | None:
        raw = await self._r.get(self._k(handle))
        if raw is None:
            return None
        c, la, ae, loc = json.loads(raw)
        if self._clock() >= ae:
            await self.delete(handle)
            return None
        return SessionData(c, la, ae, loc)

    async def touch(self, handle: str) -> SessionData | None:
        d = await self.get(handle)
        if d is None:
            return None
        d.last_active = self._clock()
        ttl = max(1, int(min(self._idle, d.absolute_expiry - d.last_active)))
        await self._r.set(
            self._k(handle),
            json.dumps([d.created, d.last_active, d.absolute_expiry, d.locale]),
            ex=ttl,
        )
        for part in ("r", "fb"):
            await self._r.expire(self._k(handle, part), ttl)
        return d

    async def delete(self, handle: str) -> None:
        keys = [self._k(handle), self._k(handle, "r"), self._k(handle, "fb")]
        async for k in self._r.scan_iter(match=self._k(handle, "m") + ":*"):
            keys.append(k)
        await self._r.delete(*keys)

    async def put_message(
        self, handle: str, message_id: str, digest: str, packet_json: str
    ) -> None:
        await self._r.set(
            f"{self._k(handle, 'm')}:{_key(message_id)}",
            json.dumps([digest, packet_json]),
            ex=self._idle,
        )

    async def get_message(self, handle: str, message_id: str) -> tuple[str, str] | None:
        raw = await self._r.get(f"{self._k(handle, 'm')}:{_key(message_id)}")
        if raw is None:
            return None
        d, p = json.loads(raw)
        return d, p

    async def add_response(self, handle: str, response_id: str) -> None:
        await self._r.sadd(self._k(handle, "r"), response_id)
        await self._r.expire(self._k(handle, "r"), self._idle)

    async def has_response(self, handle: str, response_id: str) -> bool:
        return bool(await self._r.sismember(self._k(handle, "r"), response_id))

    async def mark_feedback(self, handle: str, response_id: str) -> bool:
        added = await self._r.sadd(self._k(handle, "fb"), response_id)
        await self._r.expire(self._k(handle, "fb"), self._idle)
        return bool(added)
