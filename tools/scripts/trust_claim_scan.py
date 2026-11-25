#!/usr/bin/env python3
"""Read public pages for claims the register does not back, and for dead or placeholder links (ZID-CP-WF12 v2.0, WF-12.07, WF-12.12; TRU-01, TRU-14; CR-174).

    trust_claim_scan.py --base https://trust.example.test --page https://www.example.test/ [--page ...] [--no-links]

It fetches each page, reads the sentences that make a number, certification or superlative claim (and structured-data badges), and compares them to the approved claims the Trust Center
publishes at <base>/public/trust/v1/claims. Anything else is reported. It also follows the links of each page once and reports placeholders ("#", "javascript:") and addresses that fail.
It changes nothing and holds no credential. Exit 0 means every claim-like sentence matched an approved one and every link answered; 1 means at least one finding; 2 means a page or the
claims feed could not be read (a scan that read nothing proves nothing).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "app" / "src"))

from zid_platform.modules.trust_center.domain import monitoring as mon
from zid_platform.modules.trust_center.domain import security_txt as stx

HREF = re.compile(r"""href\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def scan(base: str, pages: list[str], *, links: bool, client: httpx.Client) -> tuple[list[str], bool]:
    """Returns the findings as printable lines and whether everything could be read."""
    out: list[str] = []
    r = client.get(base.rstrip("/") + "/public/trust/v1/claims")
    if r.status_code != 200:
        return [f"UNREADABLE the claims feed answered {r.status_code}"], False
    live = [i["text"] for i in r.json().get("items", [])]
    seen: list[mon.Observed] = []
    checked: list[tuple[str, int]] = []
    for url in pages:
        p = client.get(url)
        if p.status_code != 200:
            return [f"UNREADABLE {url} answered {p.status_code}"], False
        seen += mon.observe(url, p.text)
        if links:
            for href in sorted(set(HREF.findall(p.text))):
                if href.strip() in ("", "#") or href.startswith(("javascript:", "about:")):
                    checked.append((href, 200))
                    continue
                if href.startswith(("mailto:", "tel:")):
                    continue
                target = urljoin(url, href)
                if urlparse(target).scheme not in ("http", "https"):
                    continue
                try:
                    status = client.head(target, follow_redirects=True).status_code
                except httpx.HTTPError:
                    status = 0
                checked.append((target, status))
    out += [f"{f.code} {f.url} :: {f.sentence[:160]}" for f in mon.reconcile(seen, live, [])]
    out += [f"LINK {m}" for m in stx.dead_links(checked)]
    return out, True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--page", action="append", required=True)
    ap.add_argument("--no-links", action="store_true")
    a = ap.parse_args(argv)
    with httpx.Client(timeout=15, headers={"User-Agent": "trust-claim-scan"}) as c:
        lines, readable = scan(a.base, a.page, links=not a.no_links, client=c)
    for line in lines:
        print(line)
    if not readable:
        return 2
    print(f"{len(lines)} finding(s)")
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(main())
