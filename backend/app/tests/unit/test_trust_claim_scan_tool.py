"""The public claim scanner (tools/scripts/trust_claim_scan.py; ZID-CP-WF12 WF-12.12, QA-51 to QA-54): a rogue badge, an unapproved number and a placeholder link are found; an approved sentence is not."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx

SPEC = importlib.util.spec_from_file_location("trust_claim_scan", Path(__file__).resolve().parents[4] / "tools" / "scripts" / "trust_claim_scan.py")
tool = importlib.util.module_from_spec(SPEC)  # type: ignore[arg-type]
SPEC.loader.exec_module(tool)  # type: ignore[union-attr]

PAGE = """<html><body><p>An independent SOC 2 report is available on request.</p><p>We are 99.99% available and FedRAMP compliant.</p>
<a href="#">Responsible disclosure</a><a href="/missing">Docs</a><a href="/ok">Home</a>
<script type="application/ld+json">{"aggregateRating": {"ratingValue": 5}}</script></body></html>"""


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/public/trust/v1/claims":
        return httpx.Response(200, json={"items": [{"text": "An independent SOC 2 report is available on request."}]})
    if path == "/":
        return httpx.Response(200, text=PAGE)
    if path == "/ok":
        return httpx.Response(200, text="ok")
    return httpx.Response(404)


def test_the_scanner_reports_unapproved_claims_rogue_structured_data_and_bad_links_but_not_an_approved_sentence() -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as c:
        lines, readable = tool.scan("https://trust.test", ["https://www.test/"], links=True, client=c)
    text = "\n".join(lines)
    assert readable
    assert "UNREGISTERED_CLAIM" in text and "99.99%" in text and "FedRAMP" in text
    assert "structured data" in text.lower() and "Placeholder link" in text and "Unreachable (404)" in text
    assert "independent SOC 2 report" not in text


def test_an_unreadable_page_or_feed_is_not_a_clean_scan() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
        lines, readable = tool.scan("https://trust.test", ["https://www.test/"], links=False, client=c)
    assert readable is False and "UNREADABLE" in lines[0]
