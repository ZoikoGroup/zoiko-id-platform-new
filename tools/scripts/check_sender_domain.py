"""Check a mail sender domain's DNS before it is approved for production (Communication Estate: EMAIL TRANSPORT, section 07 "sending domain").

    python3 tools/scripts/check_sender_domain.py DOMAIN [--dkim-selector SELECTOR ...] [--json]

Reads SPF (TXT on the domain), DMARC (TXT on _dmarc.DOMAIN) and each named DKIM selector (TXT on SELECTOR._domainkey.DOMAIN) over DNS-over-HTTPS
and says what is missing or weak. It never decides which domain Zoiko uses: Security and Brand choose that, and this only tells them whether the
records they published are fit to send from. Exit 0 when nothing is wrong, 1 when a problem is found, 2 when DNS could not be read.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

Resolver = Callable[[str], list[str]]  # TXT strings for a name; raises LookupFailed when DNS cannot be read

_DOH = "https://cloudflare-dns.com/dns-query"
_SPF_LOOKUP_MECHANISMS = ("include:", "a", "mx", "ptr", "exists:", "redirect=")


class LookupFailed(Exception):
    """DNS could not be read. A failure to read is never reported as 'record missing'."""


@dataclass(frozen=True, slots=True)
class Finding:
    record: str  # SPF | DMARC | DKIM:<selector>
    level: str  # ERROR | WARN | OK
    message: str


def doh_txt(name: str, *, timeout: float = 10.0) -> list[str]:
    url = f"{_DOH}?{urllib.parse.urlencode({'name': name, 'type': 'TXT'})}"
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})  # noqa: S310 - fixed https endpoint
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LookupFailed(f"{name}: {e}") from e
    if body.get("Status") not in (0, 3):  # 0 NOERROR, 3 NXDOMAIN (no such name: no records)
        raise LookupFailed(f"{name}: resolver status {body.get('Status')}")
    return [
        "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', a["data"])) or a["data"].strip('"')
        for a in body.get("Answer", [])
        if a.get("type") == 16
    ]


def _tags(record: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in record.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def check_spf(records: Sequence[str]) -> list[Finding]:
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf:
        return [Finding("SPF", "ERROR", "no SPF record (a TXT starting v=spf1)")]
    if len(spf) > 1:
        return [Finding("SPF", "ERROR", "more than one SPF record: receivers treat this as a permanent error")]
    terms = spf[0].lower().split()
    out: list[Finding] = []
    last = terms[-1]
    if last in ("+all", "all", "?all"):
        out.append(Finding("SPF", "ERROR", f"ends with '{last}': anyone may send as this domain"))
    elif last == "~all":
        out.append(Finding("SPF", "WARN", "ends with '~all' (soft fail): use '-all' once every sender is listed"))
    elif last != "-all" and not any(t.startswith("redirect=") for t in terms):
        out.append(Finding("SPF", "ERROR", "no 'all' qualifier and no redirect: the policy for other senders is undefined"))
    if "ptr" in terms or any(t.startswith("ptr:") for t in terms):
        out.append(Finding("SPF", "WARN", "uses 'ptr', which RFC 7208 says not to use"))
    lookups = sum(1 for t in terms if t.lstrip("+-~?").startswith(_SPF_LOOKUP_MECHANISMS))
    if lookups > 10:
        out.append(Finding("SPF", "ERROR", f"{lookups} DNS-lookup terms in this record alone: the limit is 10 in total"))
    return out or [Finding("SPF", "OK", "present, single, ends in -all")]


def check_dmarc(records: Sequence[str]) -> list[Finding]:
    dmarc = [r for r in records if r.lower().startswith("v=dmarc1")]
    if not dmarc:
        return [Finding("DMARC", "ERROR", "no DMARC record on _dmarc")]
    if len(dmarc) > 1:
        return [Finding("DMARC", "ERROR", "more than one DMARC record")]
    tags = _tags(dmarc[0])
    out: list[Finding] = []
    policy = tags.get("p", "").lower()
    if policy == "none":
        out.append(Finding("DMARC", "WARN", "p=none only monitors: use quarantine or reject before relying on it"))
    elif policy not in ("quarantine", "reject"):
        out.append(Finding("DMARC", "ERROR", "missing or invalid p= policy"))
    if not tags.get("rua"):
        out.append(Finding("DMARC", "WARN", "no rua= address: nobody receives aggregate reports"))
    if tags.get("pct", "100") != "100":
        out.append(Finding("DMARC", "WARN", f"pct={tags['pct']}: the policy applies to only part of the mail"))
    for key in ("adkim", "aspf"):
        if tags.get(key, "r").lower() not in ("r", "s"):
            out.append(Finding("DMARC", "ERROR", f"{key}= must be r or s"))
    return out or [Finding("DMARC", "OK", f"p={policy}, reports to {tags.get('rua')}")]


def check_dkim(selector: str, records: Sequence[str]) -> list[Finding]:
    name = f"DKIM:{selector}"
    keys = [r for r in records if "p=" in r.replace(" ", "").lower() or r.lower().startswith("v=dkim1")]
    if not keys:
        return [Finding(name, "ERROR", "no DKIM key at this selector")]
    tags = _tags(keys[0])
    if tags.get("v", "DKIM1").upper() != "DKIM1":
        return [Finding(name, "ERROR", "v= is not DKIM1")]
    if not tags.get("p"):
        return [Finding(name, "ERROR", "empty p=: this key has been revoked")]
    if tags.get("t", "").lower() == "y":
        return [Finding(name, "WARN", "t=y marks this key as a test: receivers may ignore failures")]
    return [Finding(name, "OK", f"key present ({tags.get('k', 'rsa')})")]


def check_domain(domain: str, selectors: Sequence[str], resolve: Resolver) -> list[Finding]:
    domain = domain.strip().rstrip(".").lower()
    findings = check_spf(resolve(domain)) + check_dmarc(resolve(f"_dmarc.{domain}"))
    if not selectors:
        findings.append(Finding("DKIM", "WARN", "no --dkim-selector given: DKIM was not checked"))
    for s in selectors:
        findings += check_dkim(s, resolve(f"{s}._domainkey.{domain}"))
    return findings


def main(argv: Sequence[str] | None = None, *, resolve: Resolver = doh_txt) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("domain")
    ap.add_argument("--dkim-selector", action="append", default=[], dest="selectors")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        findings = check_domain(args.domain, args.selectors, resolve)
    except LookupFailed as e:
        print(f"could not read DNS: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.level:<5} {f.record:<18} {f.message}")
    return 1 if any(f.level == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
