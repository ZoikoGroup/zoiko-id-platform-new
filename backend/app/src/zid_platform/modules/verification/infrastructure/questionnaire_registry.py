"""Load the source of funds questionnaires (`backend/config/questionnaires/source_of_funds`; ZID_PLATFORM_CONFIG overrides the config root).

One file per version, never edited once a case has used it (a case records the content hash). The registry hands out the newest APPROVED version;
a SAMPLE version is offered only when nothing is approved, so a deployment can run the machinery in test without one being mistaken for the
compliance officer's questions. A file that does not parse stops the service rather than asking the wrong questions.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.source_of_funds import (
    Questionnaire,
    SofError,
    Status,
    parse,
)

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
DIRECTORY = "questionnaires/source_of_funds"


def _version_key(version: str) -> tuple[int, ...]:
    head = version.split("-")[0]
    return tuple(int(p) if p.isdigit() else 0 for p in head.split("."))


@lru_cache(maxsize=1)
def source_of_funds_questionnaires() -> dict[str, Questionnaire]:
    found: dict[str, Questionnaire] = {}
    for path in sorted((CONFIG / DIRECTORY).glob("*.yaml")):
        doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        q = parse(doc)
        if q.version in found:
            raise SofError("QUESTIONNAIRE_INVALID", f"version {q.version} appears twice")
        found[q.version] = q
    return found


def current_questionnaire(questionnaires: dict[str, Questionnaire] | None = None) -> Questionnaire:
    """The newest APPROVED questionnaire, else the newest SAMPLE; an error when there is none."""
    all_q = questionnaires if questionnaires is not None else source_of_funds_questionnaires()
    for wanted in (Status.APPROVED, Status.SAMPLE):
        pool = [q for q in all_q.values() if q.status is wanted]
        if pool:
            return max(pool, key=lambda q: _version_key(q.version))
    raise SofError("NO_QUESTIONNAIRE", "none is configured")


__all__ = ["current_questionnaire", "source_of_funds_questionnaires"]
