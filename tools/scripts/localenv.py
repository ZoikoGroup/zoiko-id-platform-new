"""Load backend/.env for local development scripts (never used by deployed cells, DEC-F5).

Variables already set in the shell win over the file. Empty values in the file count as unset,
so optional settings can be listed blank without breaking typed (JSON) parsing.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

BACKEND_ENV = Path(__file__).resolve().parents[2] / "backend" / ".env"


def load_backend_env(path: Path = BACKEND_ENV) -> bool:
    """Apply `path` to os.environ; returns False when the file does not exist."""
    if not path.is_file():
        return False
    for key, value in dotenv_values(path).items():
        if value and key not in os.environ:
            os.environ[key] = value
    return True
