"""Helpers for the policy pack tests that two of them share.

`captured` reads log records without the pytest logging plugin (CI runs pytest with `-p no:logging`, which removes the `caplog` fixture).
`tampered` edits a published pack behind the application's back and puts it back afterwards, so the shared test database never holds a
corrupted pack for the tests that run next (a listing of the market would otherwise refuse to serve it)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from conftest import engine
from sqlalchemy import text


@contextmanager
def captured(logger_name: str, level: int = logging.WARNING) -> Iterator[list[str]]:
    """The messages a logger emits inside the block."""
    messages: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = Collect(level)
    logger = logging.getLogger(logger_name)
    before = logger.level
    logger.addHandler(handler)
    logger.setLevel(min(level, before) if before else level)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(before)


async def _set_rules(owner_url: str, pack_id: str, sql_value: str, params: dict[str, str]) -> str:
    """Change a pack's rules as the table owner, with the guard trigger switched off for the edit only. Returns the rules it replaced."""
    async with engine(owner_url) as eng, eng.begin() as c:
        old = (
            await c.execute(
                text("SELECT rules::text FROM verify.policy_pack WHERE pack_id = :p"),
                {"p": pack_id},
            )
        ).scalar_one()
        await c.execute(text("ALTER TABLE verify.policy_pack DISABLE TRIGGER policy_pack_guard"))
        try:
            await c.execute(
                text(f"UPDATE verify.policy_pack SET rules = {sql_value} WHERE pack_id = :p"),
                {"p": pack_id, **params},
            )
        finally:
            await c.execute(text("ALTER TABLE verify.policy_pack ENABLE TRIGGER policy_pack_guard"))
    return str(old)


@contextmanager
def tampered(owner_url: str, pack_id: str) -> Iterator[None]:
    """Inside the block, the pack's stored rules no longer match its hash; afterwards they are restored exactly."""
    original = asyncio.run(
        _set_rules(owner_url, pack_id, "jsonb_set(rules, '{lawful_basis}', '\"nothing\"')", {})
    )
    try:
        yield
    finally:
        asyncio.run(_set_rules(owner_url, pack_id, "CAST(:r AS jsonb)", {"r": original}))
