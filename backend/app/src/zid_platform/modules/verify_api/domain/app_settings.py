"""An application's workflow entitlement and hosted-page settings (CR-156; ZID-EXT-IDV-001 epics E14 and E19). Pure rules, no I/O.

- Workflows: which of the approved workflows an application may start, and which one is used when a request names none. An empty allowed list means every approved workflow.
- Hosted page: a display name, ONE of a fixed set of approved themes, an https support link and a language. Never free-form colours, markup or scripts: the page a customer sees must stay recognisably a
  Zoiko iD verification page, and a partner can change its name and accent, not what the page is or does.
- Return origins: the exact https addresses the page may send a customer back to (the same rule as at creation).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain.sessions import WORKFLOWS

THEMES: dict[str, str] = {
    "blue": "Blue",
    "green": "Green",
    "violet": "Violet",
    "slate": "Slate",
}
DEFAULT_THEME = "blue"
LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
NAME_BAD = re.compile(r"[<>\x00-\x1f\x7f]")
BRANDING_KEYS = frozenset({"display_name", "theme", "support_url", "locale"})
MAX_NAME = 60
MAX_URL = 200


def check_branding(branding: dict[str, Any] | None) -> dict[str, str]:
    """The cleaned hosted-page settings, or the first thing wrong. Only the four known keys; an empty value means "not set"."""
    if branding is None:
        return {}
    if not isinstance(branding, dict):
        raise m.PartnerError("VALIDATION_FAILED", "Hosted page settings must be an object.")
    unknown = sorted(set(branding) - BRANDING_KEYS)
    if unknown:
        raise m.PartnerError(
            "VALIDATION_FAILED", f"Unknown hosted page setting: {', '.join(unknown)}."
        )
    out: dict[str, str] = {}
    name = branding.get("display_name")
    if name not in (None, ""):
        if (
            not isinstance(name, str)
            or not 2 <= len(name.strip()) <= MAX_NAME
            or NAME_BAD.search(name)
        ):
            raise m.PartnerError(
                "VALIDATION_FAILED",
                f"The display name is 2 to {MAX_NAME} characters, with no markup or control characters.",
            )
        out["display_name"] = " ".join(name.split())
    theme = branding.get("theme")
    if theme not in (None, ""):
        if theme not in THEMES:
            raise m.PartnerError("VALIDATION_FAILED", f"Choose a theme: {', '.join(THEMES)}.")
        out["theme"] = str(theme)
    url = branding.get("support_url")
    if url not in (None, ""):
        if not isinstance(url, str) or len(url) > MAX_URL:
            raise m.PartnerError("VALIDATION_FAILED", "The support link is not valid.")
        parts = urlsplit(url.strip())
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise m.PartnerError(
                "VALIDATION_FAILED", "The support link must be an https address, without a login."
            )
        out["support_url"] = url.strip()
    locale = branding.get("locale")
    if locale not in (None, ""):
        if not isinstance(locale, str) or not LOCALE.fullmatch(locale):
            raise m.PartnerError("VALIDATION_FAILED", "The language is like en or en-GB.")
        out["locale"] = locale
    return out


def check_workflows(allowed: list[str] | None, default: str | None) -> tuple[list[str], str | None]:
    """The allowed workflows (sorted, no repeats; empty = every approved one) and the default, which must be one of them."""
    if allowed is None:
        allowed = []
    if not isinstance(allowed, list) or not all(isinstance(w, str) for w in allowed):
        raise m.PartnerError(
            "VALIDATION_FAILED", "Allowed workflows must be a list of workflow ids."
        )
    unknown = [w for w in allowed if w not in WORKFLOWS]
    if unknown:
        raise m.PartnerError(
            "VALIDATION_FAILED",
            f"Unknown workflow: {', '.join(sorted(set(unknown)))}. List them with GET /v1/verify-api/workflows.",
        )
    clean = sorted(set(allowed))
    if default in (None, ""):
        return clean, None
    if default not in WORKFLOWS:
        raise m.PartnerError("VALIDATION_FAILED", f"Unknown workflow {default!r}.")
    if clean and default not in clean:
        raise m.PartnerError(
            "VALIDATION_FAILED", "The default workflow must be one of the allowed workflows."
        )
    return clean, str(default)


def entitled(allowed: list[str], workflow_id: str) -> bool:
    """May an application with this allowed list start this workflow? An empty list allows every approved one."""
    return not allowed or workflow_id in allowed


def default_workflow(allowed: list[str], default: str | None) -> str:
    """The workflow used when a request names none: the chosen default, else the platform default if allowed, else the first allowed."""
    if default:
        return default
    if entitled(allowed, "wf_document_selfie"):
        return "wf_document_selfie"
    return min(allowed)
