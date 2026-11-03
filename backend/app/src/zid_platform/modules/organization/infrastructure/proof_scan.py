"""The malware-scan hook for uploaded proof documents (WF-13B AT-12).

A scanner answers CLEAN, INFECTED or UNAVAILABLE. Only CLEAN lets a file be stored. UNAVAILABLE (the scanner is down, slow or not
configured) never lets a file through in a deployed environment: the upload is refused as retryable and nothing is stored. Local and
test may run without a scanner and then record that the file was NOT_SCANNED; that status is stored with the proof so it is never
mistaken for a clean scan.

`ClamdScanner` speaks the clamd INSTREAM protocol (length-prefixed chunks over TCP), the common self-hosted scanner. Another scanner
is a class with the same `scan` method.
"""

from __future__ import annotations

import asyncio
import struct
from enum import StrEnum
from typing import Protocol


class ScanStatus(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SCANNED = "NOT_SCANNED"  # no scanner configured, local and test only


class Scanner(Protocol):
    async def scan(self, content: bytes) -> ScanStatus: ...


class ClamdScanner:
    CHUNK = 64 * 1024

    def __init__(self, host: str, port: int = 3310, *, timeout_s: float = 10.0) -> None:
        self.host, self.port, self.timeout_s = host, port, timeout_s

    async def scan(self, content: bytes) -> ScanStatus:
        try:
            return await asyncio.wait_for(self._scan(content), self.timeout_s)
        except (TimeoutError, OSError, asyncio.IncompleteReadError):
            return ScanStatus.UNAVAILABLE

    async def _scan(self, content: bytes) -> ScanStatus:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            for i in range(0, len(content), self.CHUNK):
                chunk = content[i : i + self.CHUNK]
                writer.write(struct.pack("!I", len(chunk)) + chunk)
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            answer = (await reader.readuntil(b"\0")).rstrip(b"\0").strip()
        finally:
            writer.close()
        if answer.endswith(b"OK"):
            return ScanStatus.CLEAN
        if answer.endswith(b"FOUND"):
            return ScanStatus.INFECTED
        return ScanStatus.UNAVAILABLE  # an error reply (size limit, engine fault) is never a pass


def from_address(address: str | None) -> Scanner | None:
    """`host:port` or `host` -> a clamd scanner; nothing configured -> None."""
    if not address:
        return None
    host, _, port = address.partition(":")
    return ClamdScanner(host, int(port) if port else 3310)
