"""Capture log records in a test without pytest's logging plugin.

CI runs pytest with `-p no:logging` (a run was hanging on it), which removes the `caplog` fixture. A test that needs to
read what a module logged attaches this handler for the duration of a block instead.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager


class Captured(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(self.format(r) for r in self.records)


@contextmanager
def capture(level: int | str = logging.DEBUG, logger: str | None = None) -> Iterator[Captured]:
    """Records from `logger` (the root logger by default) at or above `level`, until the block ends."""
    target = logging.getLogger(logger)
    handler, previous = Captured(), target.level
    target.setLevel(level)
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)
