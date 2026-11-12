"""A lost dependency is a retryable 503, and only a lost dependency (ZID-GAP-001 WP-24 chaos; attack catalog rows 5 and 15)."""

from __future__ import annotations

import socket

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from zid_app import _dependency_outage


def test_a_refused_dropped_or_timed_out_connection_is_an_outage() -> None:
    assert _dependency_outage(ConnectionRefusedError())
    assert _dependency_outage(ConnectionResetError())
    assert _dependency_outage(TimeoutError())
    assert _dependency_outage(socket.gaierror("no such host"))
    assert _dependency_outage(
        OperationalError("SELECT 1", {}, Exception("server closed the connection"))
    )
    assert _dependency_outage(InterfaceError("SELECT 1", {}, Exception("connection is closed")))
    invalidated = DBAPIError("SELECT 1", {}, Exception("boom"), connection_invalidated=True)
    assert _dependency_outage(invalidated)


def test_the_outage_is_found_anywhere_in_the_chain() -> None:
    try:
        try:
            raise ConnectionRefusedError("port 5432")
        except ConnectionRefusedError as inner:
            raise RuntimeError("could not run the query") from inner
    except RuntimeError as wrapped:
        assert _dependency_outage(wrapped)


def test_a_bug_is_not_called_an_outage() -> None:
    assert not _dependency_outage(ValueError("bad input"))
    assert not _dependency_outage(KeyError("missing"))
    assert not _dependency_outage(
        FileNotFoundError("nope")
    )  # an OSError, but not a lost connection
    assert not _dependency_outage(
        DBAPIError("SELECT 1", {}, Exception("syntax error"), connection_invalidated=False)
    )


def test_a_cycle_in_the_chain_cannot_hang_the_handler() -> None:
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert not _dependency_outage(a)
