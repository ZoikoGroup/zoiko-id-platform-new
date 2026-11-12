"""Load the conformance matrix (`backend/config/regulatory/conformance-matrix.yaml`; ZID_PLATFORM_CONFIG overrides the config root; CR-083).

Every acceptance case T01..T48 of ZID-RCG-003 section 12, the tests that prove it, and whether they prove all of it (COVERED), part of it (PARTIAL)
or nothing yet (GAP). `test_conformance_matrix.py` checks every named test exists, so the matrix cannot point at nothing.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.conformance import MatrixEntry, parse_matrix

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
FILE = "regulatory/conformance-matrix.yaml"


@lru_cache(maxsize=1)
def conformance_matrix() -> tuple[MatrixEntry, ...]:
    doc: dict[str, Any] = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8")) or {}
    return tuple(parse_matrix(doc))
