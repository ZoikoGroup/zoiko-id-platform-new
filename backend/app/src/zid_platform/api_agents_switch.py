"""The one switch for API-based agents (CR-093, CR-094, CR-142, CR-144).

An API-bound agent proves a key, not the code behind it, and the rules that let it act (the token path, the assurance level that token gets, the proof a
platform-run agent rests on) are not yet signed by the security owner. So the surface ships OFF in production and is switched on by a named decision.

  ZID_API_AGENTS=auto (default)  on outside production (local, test, dev, anything else), OFF in production
  ZID_API_AGENTS=on              on everywhere
  ZID_API_AGENTS=off             off everywhere

Off means: no API client can be registered or bound, no assertion is exchanged for a token, a token already issued gets no elevated assurance, and a bound
client no longer counts as an agent's runtime proof. Nothing is deleted; switching it on again restores what was there. A process that was never told
(a script, a test) reads the same two environment variables itself, so an unconfigured production process is off, not on.
"""

from __future__ import annotations

import os

_configured: bool | None = None


def resolve(setting: str, environment: str) -> bool:
    """Whether API agents are on for this setting and environment."""
    if setting == "on":
        return True
    if setting == "off":
        return False
    return environment != "production"


def configure(setting: str, environment: str) -> bool:
    """Called once by each production entrypoint with its settings. Returns the resolved state."""
    global _configured
    _configured = resolve(setting, environment)
    return _configured


def enabled() -> bool:
    if _configured is not None:
        return _configured
    return resolve(os.environ.get("ZID_API_AGENTS", "auto"), os.environ.get("ZID_ENVIRONMENT", ""))


def reset() -> None:
    """Forget what was configured (tests)."""
    global _configured
    _configured = None
