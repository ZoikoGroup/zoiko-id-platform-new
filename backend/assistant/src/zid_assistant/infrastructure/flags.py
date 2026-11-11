"""Feature flags and the independent kill switches (ADR-14, INV-07, OD-018/021).

Defaults come from settings (all OFF). A runtime override store lets an operator flip a switch without a deploy:
  python -m zid_assistant.flags set kill_switch on
The override lives in Valkey (shared across replicas) or in process memory for local use. The effective value is
`settings OR-ed with nothing`: an override can turn a flag OFF or ON, but the kill switch always wins over every other flag.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import valkey.asyncio as valkey_asyncio

from zid_assistant.settings import AssistantSettings

FLAG_NAMES = ("enabled", "ai_answers", "intake_sales", "intake_support", "marketing", "kill_switch")
_HASH = "zpa:flags"


@dataclass(frozen=True, slots=True)
class Flags:
    enabled: bool
    ai_answers: bool
    intake_sales: bool
    intake_support: bool
    marketing: bool
    kill_switch: bool

    @property
    def chat_on(self) -> bool:
        return self.enabled and not self.kill_switch

    @property
    def ai_on(self) -> bool:
        return self.chat_on and self.ai_answers

    @property
    def sales_on(self) -> bool:
        return self.chat_on and self.intake_sales

    @property
    def support_on(self) -> bool:
        return self.chat_on and self.intake_support

    @property
    def marketing_on(self) -> bool:
        return self.chat_on and self.marketing and (self.intake_sales or self.intake_support)


class FlagStore:
    def __init__(
        self,
        settings: AssistantSettings,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 5.0,
    ) -> None:
        self._s = settings
        self._clock = clock
        self._ttl = ttl
        self._r = (
            valkey_asyncio.from_url(settings.valkey_url, decode_responses=True)  # type: ignore[no-untyped-call]
            if settings.valkey_url
            else None
        )
        self._mem: dict[str, bool] = {}
        self._cached: tuple[float, Flags] | None = None

    def _base(self) -> dict[str, bool]:
        return {n: bool(getattr(self._s, n)) for n in FLAG_NAMES}

    async def get(self) -> Flags:
        now = self._clock()
        if self._cached and now - self._cached[0] < self._ttl:
            return self._cached[1]
        vals = self._base()
        try:
            over = await self._r.hgetall(_HASH) if self._r is not None else {}
        except Exception:  # noqa: BLE001 - a flag store outage must never open a feature: keep the safe defaults
            over = {}  # defaults stand: settings already encode the safe state
        for k, v in {**{k: ("1" if b else "0") for k, b in self._mem.items()}, **over}.items():
            if k in vals:
                vals[k] = v in ("1", "true", "on", "yes")
        flags = Flags(**vals)
        self._cached = (now, flags)
        return flags

    async def set(self, name: str, value: bool) -> None:
        if name not in FLAG_NAMES:
            raise ValueError(f"unknown flag: {name}")
        if self._r is not None:
            await self._r.hset(_HASH, name, "1" if value else "0")
        else:
            self._mem[name] = value
        self._cached = None

    async def clear(self, name: str) -> None:
        if self._r is not None:
            await self._r.hdel(_HASH, name)
        self._mem.pop(name, None)
        self._cached = None
