"""A source that fails is unavailable, never "not registered" and never a pass (WF02 section 20; acceptance T02, T03, T34).

Every way a register can fail, as the adapter sees it: refused, throttled, broken, slow, redirected somewhere else, too large, not JSON, the wrong shape, no credential configured. Each is
SOURCE_UNAVAILABLE, so the case goes to review or an alternative; none becomes a declined company or a verified one."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from zid_platform.modules.organization.domain.kyb import KybError
from zid_platform.modules.organization.infrastructure.kyb_adapters import CompaniesHouseKyb

NOW = datetime(2026, 11, 17, tzinfo=UTC)
GOOD = {
    "company_number": "00123456",
    "company_name": "ACME WIDGETS LIMITED",
    "company_status": "active",
    "type": "ltd",
    "registered_office_address": {"address_line_1": "1 High St", "locality": "London"},
}


def adapter(handler, key="k") -> CompaniesHouseKyb:
    return CompaniesHouseKyb(api_key=lambda: key, transport=httpx.MockTransport(handler))


def get(a: CompaniesHouseKyb, number="00123456"):
    return asyncio.run(a.get(number, "GB", NOW))


def unavailable(a: CompaniesHouseKyb) -> str:
    with pytest.raises(KybError) as e:
        get(a)
    assert e.value.code == "SOURCE_UNAVAILABLE"
    return e.value.detail


def test_a_good_answer_is_a_record_and_a_404_is_the_only_not_found() -> None:
    assert get(adapter(lambda r: httpx.Response(200, json=GOOD))).registration_number == "00123456"
    assert (
        get(adapter(lambda r: httpx.Response(404))) is None
    )  # the register says there is no such company: the one answer that is "not found"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 504, 400, 410])
def test_a_refusal_a_throttle_or_a_server_error_is_unavailable_never_not_found(
    status: int,
) -> None:  # T02
    assert str(status) in unavailable(adapter(lambda r, s=status: httpx.Response(s)))


def test_a_timeout_or_a_dropped_connection_is_unavailable() -> None:  # T02
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def dropped(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert unavailable(adapter(slow)) == "ReadTimeout"
    assert unavailable(adapter(dropped)) == "ConnectError"


def test_a_redirect_is_not_followed_so_a_mirror_cannot_answer_for_the_register() -> None:  # T07
    seen: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        return httpx.Response(302, headers={"location": "https://evil.example/company/00123456"})

    assert "302" in unavailable(adapter(redirect))
    assert seen == [
        "api.company-information.service.gov.uk"
    ]  # nothing was requested from the other host


def test_an_oversized_unreadable_or_oddly_shaped_answer_is_unavailable() -> None:
    assert "too large" in unavailable(
        adapter(lambda r: httpx.Response(200, content=b'{"a":"' + b"x" * 3_000_000 + b'"}'))
    )
    assert "not JSON" in unavailable(
        adapter(lambda r: httpx.Response(200, content=b"<html>maintenance</html>"))
    )
    assert "shape" in unavailable(adapter(lambda r: httpx.Response(200, json=["a", "b"])))


def test_a_missing_credential_makes_the_source_unavailable_and_nothing_is_looked_up() -> (
    None
):  # T03
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=GOOD)

    for key in (None, ""):
        assert "no Companies House credential" in unavailable(adapter(handler, key=key))
    assert calls == []  # no request left the platform without a credential: no lookup is fabricated


def test_a_market_the_source_does_not_cover_is_not_asked() -> None:
    called: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, json=GOOD)

    assert asyncio.run(adapter(handler).get("00123456", "FR", NOW)) is None
    assert called == []
