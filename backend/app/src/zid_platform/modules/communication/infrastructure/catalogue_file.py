"""Reads the template catalogue from `templates/catalogue.json` (the copybook, structured by
`tools/scripts/build_email_catalogue.py`). The domain layer parses and validates; only this module touches a file."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from zid_platform.modules.communication.domain.catalogue import Catalogue, parse

CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "templates" / "catalogue.json"


@lru_cache(maxsize=1)
def load(path: Path = CATALOGUE_PATH) -> Catalogue:
    return parse(json.loads(path.read_text(encoding="utf-8")))
