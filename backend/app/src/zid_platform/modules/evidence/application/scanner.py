"""Malware scanning for uploaded evidence (ZID-INT-002 section 05). Fail closed: only an explicit CLEAN answer lets bytes be kept.

- `ClamdScanner` speaks the real clamd INSTREAM protocol over TCP (ClamAV is free and open source; run `clamd` and point the cell at it).
- `SimulatorScanner` recognizes the standard EICAR test string and nothing else. It is for local and test environments ONLY and is
  never wired in a deployed environment, so a deployed cell with no scanner refuses uploads instead of pretending to scan.
- Anything else that goes wrong (the scanner is down, answers garbage or times out) is SCAN_ERROR, never CLEAN.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Protocol

CHUNK = 64 * 1024
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass(frozen=True, slots=True)
class Verdict:
    state: str  # CLEAN | INFECTED | SCAN_ERROR
    scanner: str
    signature: str | None = None


class Scanner(Protocol):
    name: str

    async def scan(self, content: bytes) -> Verdict: ...


class SimulatorScanner:
    """Finds the EICAR test string. It cannot find real malware: local and test only."""

    name = "simulator-eicar"

    async def scan(self, content: bytes) -> Verdict:
        if EICAR in content:
            return Verdict("INFECTED", self.name, "Eicar-Test-Signature")
        return Verdict("CLEAN", self.name)


class ClamdScanner:
    """clamd over TCP using the INSTREAM command (length-prefixed chunks, a zero-length chunk to end)."""

    name = "clamd"

    def __init__(self, host: str, port: int = 3310, timeout: float = 20.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    async def scan(self, content: bytes) -> Verdict:
        try:
            return await asyncio.wait_for(self._scan(content), self.timeout)
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            return Verdict("SCAN_ERROR", self.name)

    async def _scan(self, content: bytes) -> Verdict:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            for i in range(0, len(content), CHUNK):
                part = content[i : i + CHUNK]
                writer.write(struct.pack(">I", len(part)) + part)
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = (await reader.readuntil(b"\0")).rstrip(b"\0").decode("ascii", "replace").strip()
        finally:
            writer.close()
        return parse_reply(reply, self.name)


def parse_reply(reply: str, scanner: str) -> Verdict:
    """`stream: OK`, `stream: <signature> FOUND`, or anything else (an ERROR, a size limit, garbage) which is SCAN_ERROR."""
    if reply == "stream: OK":
        return Verdict("CLEAN", scanner)
    if reply.startswith("stream: ") and reply.endswith(" FOUND"):
        return Verdict("INFECTED", scanner, reply[len("stream: ") : -len(" FOUND")])
    return Verdict("SCAN_ERROR", scanner)


def scanner_from_address(address: str) -> ClamdScanner:
    """`host` or `host:port` (the form ZID_PROOF_SCANNER_ADDRESS already uses) to a clamd scanner; the default port is 3310."""
    host, _, port = address.partition(":")
    return ClamdScanner(host, int(port) if port else 3310)


async def scan(scanner: Scanner | None, content: bytes) -> Verdict:
    """Scan, or say plainly that nothing could. No scanner, or any failure inside one, is SCAN_ERROR."""
    if scanner is None:
        return Verdict("SCAN_ERROR", "none")
    try:
        v = await scanner.scan(content)
    except Exception:  # noqa: BLE001 - a scanner that breaks has not said the file is clean
        return Verdict("SCAN_ERROR", getattr(scanner, "name", "unknown"))
    return v if v.state in {"CLEAN", "INFECTED", "SCAN_ERROR"} else Verdict("SCAN_ERROR", v.scanner)
