"""Load the verification workflow definitions (`backend/config/workflows`; ZID_PLATFORM_CONFIG overrides the config root).

One file per workflow version. A file that does not parse stops the service rather than loading half a workflow. The shipped file is a SAMPLE and is
not approved: loading a definition never publishes it.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from zid_platform.modules.verification.domain.workflow import Workflow, WorkflowError, parse

CONFIG = Path(
    os.environ.get("ZID_PLATFORM_CONFIG") or Path(__file__).resolve().parents[6] / "config"
)
DIRECTORY = "workflows"


@lru_cache(maxsize=1)
def workflow_definitions() -> dict[tuple[str, str], Workflow]:
    found: dict[tuple[str, str], Workflow] = {}
    for path in sorted((CONFIG / DIRECTORY).glob("*.yaml")):
        doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        wf = parse(doc)
        key = (wf.workflow_id, wf.version)
        if key in found:
            raise WorkflowError("WORKFLOW_INVALID", f"{key[0]} {key[1]} appears twice")
        found[key] = wf
    return found


__all__ = ["workflow_definitions"]
