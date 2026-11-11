"""Outbox delivery to the approved sales / support receivers (INT-010 section 04 WF-10A/B, EW outbox flow).

Adapters are vendor-neutral. `LogOnlyAdapter` is the local stand-in: it records that a request was handled but sends nothing anywhere
(there is no CRM or help desk yet: OD-014). `WebhookAdapter` posts a minimal, signed payload to an allowlisted https receiver and
treats only a 2xx as "accepted". A visitor is never told "delivered"; the receiver's own case id stays private.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from zid_assistant.application.intake import ADAPTER_REV, IntakeCrypto
from zid_assistant.infrastructure.store import OutboxRow, Store
from zid_assistant.settings import AssistantSettings

log = logging.getLogger("zid.assistant.delivery")
_ALLOWED_PORTS = frozenset({None, 443, 8443})


@dataclass(frozen=True, slots=True)
class Receipt:
    recipient_ref: str
    response_code: int
    correlation_id: str | None = None


class PermanentRejection(Exception):
    """The receiver refused the request for good (4xx other than 408/429): no retry."""


class TemporaryFailure(Exception):
    """Timeout, connection error, 408, 429 or 5xx: retry with backoff."""


class Adapter(Protocol):
    async def deliver(
        self, request_id: str, purpose: str, country: str, fields: dict[str, str]
    ) -> Receipt: ...


class LogOnlyAdapter:
    async def deliver(
        self, request_id: str, purpose: str, country: str, fields: dict[str, str]
    ) -> Receipt:
        # Redacted: no field values, no address (ADR-12).
        log.info(
            "intake handled by the log-only adapter",
            extra={"purpose": purpose, "country": country, "field_names": sorted(fields)},
        )
        return Receipt("log-only", 200)


def check_receiver_url(url: str, *, allow_local_http: bool) -> None:
    """https only, a DNS host (never an IP literal), no credentials, standard port. Local/test may use http://localhost."""
    parts = urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        raise ValueError("receiver url must have a host and no credentials")
    if allow_local_http and parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"):
        return
    if parts.scheme != "https" or parts.port not in _ALLOWED_PORTS:
        raise ValueError("receiver url must be https on a standard port")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return
    raise ValueError("receiver url must not be an IP literal")


async def assert_public_host(host: str) -> None:
    """Re-checked right before connecting, so a rebinding record cannot slip a private address in later."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            raise ValueError("receiver resolves to a non-public address")


class WebhookAdapter:
    def __init__(
        self,
        url: str,
        secret: bytes,
        *,
        allow_local_http: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        check_receiver_url(url, allow_local_http=allow_local_http)
        self._url, self._secret, self._local, self._transport = (
            url,
            secret,
            allow_local_http,
            transport,
        )

    async def deliver(
        self, request_id: str, purpose: str, country: str, fields: dict[str, str]
    ) -> Receipt:
        host = urlsplit(self._url).hostname or ""
        if self._transport is None and not (self._local and host in ("localhost", "127.0.0.1")):
            try:
                await assert_public_host(host)
            except (ValueError, OSError) as e:
                raise PermanentRejection("receiver_not_public") from e
        body = json.dumps(
            {
                "request_id": request_id,
                "purpose": purpose,
                "country_code": country,
                "fields": fields,
                "adapter_rev": ADAPTER_REV,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        sig = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        headers = {
            "content-type": "application/json",
            "idempotency-key": request_id,
            "x-zid-signature": f"sha256={sig}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False, transport=self._transport
            ) as c:
                r = await c.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise TemporaryFailure("transport") from e
        if 200 <= r.status_code < 300:
            ref = "receiver"
            with suppress(
                ValueError, AttributeError
            ):  # a receiver that returns no JSON is still an acceptance
                ref = str(r.json().get("case_ref") or ref)
            return Receipt(ref[:100], r.status_code, r.headers.get("x-correlation-id"))
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise TemporaryFailure(f"status_{r.status_code}")
        raise PermanentRejection(f"status_{r.status_code}")


def build_adapters(settings: AssistantSettings) -> dict[str, Adapter]:
    adapters: dict[str, Adapter] = {}
    secret = b""
    secret_file = getattr(settings, "intake_webhook_secret_file", None)
    if secret_file is not None:
        secret = secret_file.read_text(encoding="utf-8").strip().encode()
    for purpose in ("sales", "support"):
        url = settings.intake_destinations.get(purpose)
        if url:
            adapters[f"{purpose}-receiver"] = WebhookAdapter(
                url, secret, allow_local_http=not settings.is_production_like
            )
        else:
            adapters[f"{purpose}-receiver"] = LogOnlyAdapter()
    return adapters


class OutboxWorker:
    """Claims due outbox rows and delivers them. Idempotent: a crash between send and mark simply re-sends with the same key."""

    def __init__(
        self, store: Store, crypto: IntakeCrypto, adapters: dict[str, Adapter], max_attempts: int
    ) -> None:
        self._store, self._crypto, self._adapters, self._max = store, crypto, adapters, max_attempts

    async def run_once(self, now: datetime | None = None, limit: int = 20) -> int:
        now = now or datetime.now(UTC)
        handled = 0
        for row in await self._store.due_outbox(now, limit):
            await self._handle(row, now)
            handled += 1
        return handled

    async def _handle(self, row: OutboxRow, now: datetime) -> None:
        loaded = await self._store.intake_fields(row.request_id)
        if loaded is None:  # erased or missing: nothing lawful left to send
            await self._store.mark_failed(
                row.outbox_id, row.request_id, "REJECTED", row.attempts, None, "erased_or_missing"
            )
            return
        purpose, country, blob = loaded
        adapter = self._adapters.get(row.target)
        if adapter is None:
            await self._store.mark_failed(
                row.outbox_id, row.request_id, "DEAD_LETTER", row.attempts, None, "no_adapter"
            )
            return
        try:
            receipt = await adapter.deliver(
                row.request_id, purpose, country, self._crypto.open(blob)
            )
        except PermanentRejection as e:
            await self._store.mark_failed(
                row.outbox_id, row.request_id, "REJECTED", row.attempts + 1, None, str(e)
            )
            return
        except TemporaryFailure as e:
            attempts = row.attempts + 1
            if attempts >= self._max:
                await self._store.mark_failed(
                    row.outbox_id, row.request_id, "DEAD_LETTER", attempts, None, str(e)
                )
            else:
                await self._store.mark_failed(
                    row.outbox_id,
                    row.request_id,
                    "RETRYING",
                    attempts,
                    now + timedelta(seconds=min(900, 30 * 2**attempts)),
                    str(e),
                )
            return
        await self._store.mark_delivered(
            row.outbox_id,
            row.request_id,
            receipt.recipient_ref,
            receipt.response_code,
            receipt.correlation_id,
        )


async def worker_loop(worker: OutboxWorker, stop: asyncio.Event, interval: float = 5.0) -> None:
    while not stop.is_set():
        try:
            await worker.run_once()
        except Exception:
            log.exception("outbox batch failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
