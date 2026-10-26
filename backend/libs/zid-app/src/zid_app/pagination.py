"""Keyset pagination (ZID-API-001 §6; API-17).

A list is walked with an opaque cursor that names the last row returned as `(sort value, id)` and the next page is
"everything after that row" in the list's total order (`ORDER BY sort DESC, id DESC`). Because the position is a key
and not an offset, rows inserted or removed while a client is paging cannot make it repeat or skip a row that was
there when it started:

- a row inserted ahead of the cursor does not shift the following pages;
- a row inserted behind it is seen later, once, in its place in the order.
A cursor is bound to the list it came from (`scope`); one from another list, or a malformed one, is refused.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")


class InvalidCursor(ValueError):
    """The cursor is malformed or belongs to another list."""


def encode_cursor(scope: str, sort_value: str, row_id: str) -> str:
    raw = json.dumps([scope, sort_value, row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(token: str, scope: str) -> tuple[str, str]:
    """`(sort value, row id)` from a cursor for this list; raises InvalidCursor otherwise."""
    try:
        data: Any = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, TypeError) as e:
        raise InvalidCursor("cursor is not valid") from e
    if (
        not isinstance(data, list)
        or len(data) != 3
        or not all(isinstance(x, str) for x in data)
        or data[0] != scope
    ):
        raise InvalidCursor("cursor is not valid for this list")
    return data[1], data[2]


def page_of[T](
    rows: Sequence[T], limit: int, key_of: Callable[[T], tuple[str, str]], scope: str
) -> tuple[list[T], str | None]:
    """Split `limit + 1` fetched rows into the page and the cursor of the next one (None at the end)."""
    items = list(rows[:limit])
    if len(rows) <= limit or not items:
        return items, None
    sort_value, row_id = key_of(items[-1])
    return items, encode_cursor(scope, sort_value, row_id)


__all__ = ["InvalidCursor", "decode_cursor", "encode_cursor", "page_of"]
