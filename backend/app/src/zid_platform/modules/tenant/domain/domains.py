"""Enterprise domain verification rules (ZID-OVERLAY-001 §5, §24, §31; OVL-15; G-W2).

Pure rules, no I/O:
- control is proven by a random, expiring challenge published in DNS (TXT) or at an exact HTTPS
  well-known URL; an admin-email challenge is convenience only and is not offered here;
- verified domain control is *not* organization identity or authority (no authority semantics);
- consumer/public mail domains can never be claimed; IP literals and single labels are refused;
- a domain is VERIFIED in at most one tenant of a cell at a time: no first-come capture of a
  domain another tenant controls - a conflict goes to the explicit administrative model;
- verification is re-checked; lost control makes it STALE (never a silent ownership transfer).
"""

from __future__ import annotations

import ipaddress
import re
from datetime import timedelta
from enum import StrEnum

from zid_platform.modules.tenant.domain.lifecycle import TenantError

CHALLENGE_TTL = timedelta(days=7)
REVERIFY_EVERY = timedelta(days=30)
TXT_PREFIX = "_zoiko-challenge"
TXT_VALUE = "zoiko-domain-verification="
WELL_KNOWN = "/.well-known/zoiko-domain-verification"
LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

# §5: consumer/public email domains cannot be claimed as enterprise tenant domains.
CONSUMER_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.co.uk",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.de",
        "web.de",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "yandex.ru",
        "qq.com",
        "163.com",
    }
)


class Method(StrEnum):
    DNS_TXT = "DNS_TXT"
    HTTPS_WELL_KNOWN = "HTTPS_WELL_KNOWN"


class DomainStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    STALE = "STALE"  # control could not be re-proven: claims/routing reviewed, nothing transferred
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


def normalize_domain(value: str) -> str:
    raw = value.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(raw.strip("[]"))
    except ValueError:
        pass
    else:
        raise TenantError("DOMAIN_NOT_CLAIMABLE", "IP addresses are not domains.")
    try:
        ascii_name = raw.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise TenantError("VALIDATION_FAILED", "Not a valid domain name.") from e
    labels = ascii_name.split(".")
    if len(labels) < 2 or len(ascii_name) > 253 or not all(LABEL.fullmatch(lb) for lb in labels):
        raise TenantError("VALIDATION_FAILED", "Not a valid domain name.")
    if ascii_name in CONSUMER_DOMAINS or labels[-1].isdigit():
        raise TenantError("DOMAIN_NOT_CLAIMABLE", "Consumer/public mail domains cannot be claimed.")
    return ascii_name


def instructions(domain: str, method: Method, token: str) -> dict[str, str]:
    if method is Method.DNS_TXT:
        return {"record_type": "TXT", "name": f"{TXT_PREFIX}.{domain}", "value": TXT_VALUE + token}
    return {"url": f"https://{domain}{WELL_KNOWN}", "body": token}


def txt_proves(records: list[str], token: str) -> bool:
    return any(r.strip().strip('"') == TXT_VALUE + token for r in records)


def body_proves(body: str, token: str) -> bool:
    return body.strip() == token
