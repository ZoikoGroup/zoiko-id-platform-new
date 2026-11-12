"""Load the regulated product line registry (`backend/config/product-lines`; ZID_PLATFORM_CONFIG overrides the config root).

The shipped file lists every line as DESIGN. A line that is switched on without its legal basis, partner contract or approver does not
load: the service refuses to start rather than quietly run a regulated activity.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.product_lines import LineRegistry, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "product-lines/product-lines.yaml"


@lru_cache(maxsize=1)
def product_line_registry() -> LineRegistry:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return parse(doc)


__all__ = ["CONFIG", "product_line_registry"]
