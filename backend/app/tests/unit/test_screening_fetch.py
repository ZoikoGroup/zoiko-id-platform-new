"""Fetching sanctions list files (ZID-ED-04 section 07): only verified addresses, only the hosts a source names, bounded and
whole; every failure is a code that leaves the list in use alone."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from zid_platform.modules.screening.domain.lists import ListId
from zid_platform.modules.screening.infrastructure.config import Source
from zid_platform.modules.screening.infrastructure.fetch import MAX_BYTES, FetchError, fetch_source

UN = Source(
    ListId.UN_SC,
    {"file": "https://scsanctions.un.org/resources/xml/en/consolidated.xml"},
    ("blob.example.net",),
    "xml",
)


def run(source: Source, handler: object) -> dict[str, bytes]:
    return dict(asyncio.run(fetch_source(source, transport=httpx.MockTransport(handler))))  # type: ignore[arg-type]


def test_a_redirect_to_a_host_the_source_names_is_followed_and_the_body_returned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "scsanctions.un.org":
            return httpx.Response(
                302, headers={"location": "https://blob.example.net/f.xml?sig=abc"}
            )
        assert request.url.host == "blob.example.net" and request.url.params["sig"] == "abc"
        return httpx.Response(200, content=b"<CONSOLIDATED_LIST/>")

    assert run(UN, handler) == {"file": b"<CONSOLIDATED_LIST/>"}


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("https://evil.example/x.xml", "FETCH_REDIRECT_REFUSED"),  # a host the source does not name
        ("http://blob.example.net/x.xml", "FETCH_BLOCKED"),  # not https
        ("https://169.254.169.254/latest", "FETCH_BLOCKED"),  # an IP literal, the metadata service
        ("https://blob.example.net:9999/x.xml", "FETCH_BLOCKED"),  # an unusual port
    ],
)
def test_a_redirect_anywhere_else_is_refused(location: str, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(FetchError) as caught:
        run(UN, handler)
    assert caught.value.code == code


def test_a_redirect_loop_stops() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://blob.example.net/again"})

    with pytest.raises(FetchError) as caught:
        run(UN, handler)
    assert caught.value.code == "FETCH_REDIRECT_REFUSED"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500), "FETCH_FAILED"),
        (httpx.Response(404), "FETCH_FAILED"),
        (httpx.Response(200, content=b""), "FETCH_FAILED"),
        (httpx.Response(302), "FETCH_FAILED"),  # a redirect with nowhere to go
        (httpx.Response(200, content=b"x" * (MAX_BYTES + 1)), "FETCH_TOO_LARGE"),
    ],
)
def test_an_unusable_answer_is_a_code_not_a_list(response: httpx.Response, code: str) -> None:
    with pytest.raises(FetchError) as caught:
        run(UN, lambda request: response)
    assert caught.value.code == code


def test_a_transport_failure_is_a_code() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(FetchError) as caught:
        run(UN, boom)
    assert caught.value.code == "FETCH_FAILED"


def test_a_source_with_no_verified_address_is_never_fetched() -> None:
    nothing = Source(ListId.OFAC_SDN, {}, (), "csv")
    half = Source(
        ListId.OFAC_SDN, {"alt": "https://example.org/alt.csv"}, (), "csv"
    )  # no SDN file among them
    for source in (nothing, half):
        with pytest.raises(FetchError) as caught:
            asyncio.run(fetch_source(source))
        assert caught.value.code == "NO_ADDRESS"


def test_the_three_ofac_files_load_together() -> None:
    source = Source(
        ListId.OFAC_SDN,
        {
            "sdn": "https://ofac.example.gov/SDN.CSV",
            "alt": "https://ofac.example.gov/ALT.CSV",
            "add": "https://ofac.example.gov/ADD.CSV",
        },
        (),
        "csv",
    )
    got = run(source, lambda request: httpx.Response(200, content=request.url.path.encode()))
    assert got == {"sdn": b"/SDN.CSV", "alt": b"/ALT.CSV", "add": b"/ADD.CSV"}
