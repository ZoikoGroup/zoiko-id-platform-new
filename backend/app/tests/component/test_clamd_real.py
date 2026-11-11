"""The real clamd (ClamAV, free and open source) through the real INSTREAM protocol. Skipped unless ZID_TEST_CLAMD is set (host:port), e.g.
    docker run -d -p 127.0.0.1:3310:3310 clamav/clamav:stable      # then ZID_TEST_CLAMD=127.0.0.1:3310
It proves what the EICAR simulator cannot: that the code works against the genuine daemon, including the real signature name."""

from __future__ import annotations

import asyncio
import os

import pytest
from zid_platform.modules.evidence.application.scanner import EICAR, scan, scanner_from_address

ADDRESS = os.environ.get("ZID_TEST_CLAMD")
pytestmark = pytest.mark.skipif(not ADDRESS, reason="ZID_TEST_CLAMD is not set")


def test_the_real_clamd_finds_eicar_and_passes_clean_bytes_and_a_dead_port_is_an_error() -> None:
    s = scanner_from_address(ADDRESS or "")
    clean = asyncio.run(scan(s, b"%PDF-1.7 a perfectly ordinary document"))
    assert clean.state == "CLEAN" and clean.scanner == "clamd"
    # ClamAV recognises the standard EICAR file as a whole (the simulator flags the string anywhere, which is stricter than the real daemon)
    bad = asyncio.run(scan(s, EICAR))
    assert bad.state == "INFECTED" and "eicar" in (bad.signature or "").lower()
    big = asyncio.run(scan(s, b"A" * (3 * 1024 * 1024)))  # several chunks
    assert big.state == "CLEAN"
    dead = asyncio.run(scan(scanner_from_address("127.0.0.1:1"), b"x"))
    assert dead.state == "SCAN_ERROR", "an unreachable scanner never says clean"
