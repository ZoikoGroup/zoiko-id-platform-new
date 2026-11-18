"""The issuer trust list, the QR fetch and the combined check (WF02 K03; acceptance T06, T07)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from zid_platform.modules.organization.application import extract_check as xc
from zid_platform.modules.organization.domain import extract_authenticity as ea
from zid_platform.modules.organization.infrastructure import issuer_trust as it
from zid_platform.modules.organization.infrastructure import qr_fetch

NOW = datetime(2026, 11, 17, tzinfo=UTC)
KEY = ed25519.Ed25519PrivateKey.generate()
PEM = (
    KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
HOST = "find-and-update.company-information.service.gov.uk"
URL = f"https://{HOST}/company/00123456"
CONTENT = b"%PDF-1.7 the extract"


def doc(**over):
    entry = {
        "id": "uk-ch",
        "hosts": [HOST],
        "path_prefixes": ["/company/"],
        "keys": [
            {
                "id": "k1",
                "algorithm": "Ed25519",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "public_key_pem": PEM,
            }
        ],
        **over,
    }
    return {"issuers": [entry]}


def test_the_shipped_trust_list_is_empty_so_nothing_is_trusted() -> None:
    assert it.load() == {}
    assert it.parse(None) == {} and it.parse({}) == {} and it.parse({"issuers": []}) == {}


def test_a_good_entry_loads_with_its_keys_hosts_and_paths() -> None:
    issuers = it.parse(doc())
    issuer = issuers["uk-ch"]
    assert issuer.trusted_hosts == {HOST} and issuer.path_prefixes == ("/company/",)
    assert (
        issuer.keys[0].key_id == "k1"
        and issuer.keys[0].algorithm == "Ed25519"
        and issuer.keys[0].valid_to is None
    )


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ({"id": "bad id!"}, "ISSUER_ID_INVALID"),
        ({"hosts": ["*.gov.uk"]}, "HOST_INVALID"),
        ({"hosts": ["Find.Gov.UK"]}, "HOST_INVALID"),
        ({"hosts": ["203.0.113.9"]}, "HOST_INVALID"),
        ({"hosts": ["localhost"]}, "HOST_INVALID"),
        ({"path_prefixes": ["company/"]}, "PATH_PREFIX_INVALID"),
        (
            {
                "keys": [
                    {
                        "id": "k",
                        "algorithm": "RS256",
                        "valid_from": "2026-01-01T00:00:00+00:00",
                        "public_key_pem": PEM,
                    }
                ]
            },
            "ALGORITHM_NOT_ALLOWED",
        ),
        (
            {
                "keys": [
                    {
                        "id": "k",
                        "algorithm": "Ed25519",
                        "valid_from": "2026-01-01T00:00:00+00:00",
                        "public_key_pem": "not a key",
                    }
                ]
            },
            "KEY_UNREADABLE",
        ),
        ({"keys": [{"id": "k", "algorithm": "Ed25519", "public_key_pem": PEM}]}, "KEY_UNREADABLE"),
    ],
)
def test_a_malformed_entry_refuses_the_whole_list(entry: dict, why: str) -> None:
    with pytest.raises(it.TrustListError, match=why):
        it.parse(doc(**entry))


def test_one_bad_entry_among_good_ones_loads_nothing() -> None:
    both = {"issuers": [doc()["issuers"][0], {"id": "second", "hosts": ["NOT LOWER.example"]}]}
    with pytest.raises(it.TrustListError):
        it.parse(both)
    repeated = {"issuers": [doc()["issuers"][0], doc()["issuers"][0]]}
    with pytest.raises(it.TrustListError, match="ISSUER_REPEATED"):
        it.parse(repeated)


def transport(status: int = 200, body: bytes = CONTENT, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def issuer() -> ea.Issuer:
    return it.parse(doc())["uk-ch"]


def fetch(url: str, **kw):
    return asyncio.run(qr_fetch.fetch_digest(url, issuer(), **kw))


def test_the_issuers_own_address_is_fetched_and_digested() -> None:
    assert fetch(URL, transport=transport()) == xc.digest(CONTENT)


def test_an_address_that_is_not_the_issuers_is_never_requested() -> None:  # T07
    seen: list[str] = []
    for url in (
        "https://evil.example/company/1",
        f"https://{HOST}@evil.example/company/1",
        "http://" + HOST + "/company/1",
        f"https://{HOST}/login",
        "https://169.254.169.254/latest/meta-data/",
    ):
        assert fetch(url, transport=transport(seen=seen)) is None, url
    assert seen == []  # nothing left the platform: a QR cannot make us call an arbitrary host


def test_any_failure_is_none_never_an_error_or_a_digest_of_an_error_page() -> None:
    for status in (301, 302, 401, 403, 404, 429, 500, 503):
        assert fetch(URL, transport=transport(status=status)) is None, status
    assert fetch(URL, transport=transport(body=b"")) is None
    assert fetch(URL, transport=transport(body=b"x" * (qr_fetch.MAX_BYTES + 1))) is None

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    assert fetch(URL, transport=httpx.MockTransport(boom)) is None


def run_check(**kw):
    return asyncio.run(xc.check(CONTENT, issuer_id="uk-ch", issuers=it.parse(doc()), now=NOW, **kw))


def test_a_signed_extract_is_the_issuers_word_and_an_edited_one_is_not() -> None:  # T06
    sig = KEY.sign(CONTENT)
    ok = run_check(signature=sig, key_id="k1", algorithm="Ed25519")
    assert ok.result is ea.Authenticity.ISSUER_SIGNED
    edited = asyncio.run(
        xc.check(
            CONTENT + b" edited",
            issuer_id="uk-ch",
            issuers=it.parse(doc()),
            signature=sig,
            key_id="k1",
            algorithm="Ed25519",
            now=NOW,
        )
    )
    assert edited.result is ea.Authenticity.NOT_PROVEN and edited.reasons == (
        "SIGNATURE_DOES_NOT_VERIFY",
    )


def test_a_qr_is_confirmed_only_when_the_issuers_own_address_returns_the_same_content() -> (
    None
):  # T07
    assert (
        run_check(qr_url=URL, transport=transport()).result
        is ea.Authenticity.ISSUER_CONFIRMED_BY_QR
    )
    assert (
        run_check(qr_url=URL, transport=transport(body=b"a different record")).result
        is ea.Authenticity.CONTENT_MISMATCH
    )
    down = run_check(qr_url=URL, transport=transport(status=503))
    assert down.result is ea.Authenticity.NOT_PROVEN and down.reasons == (
        "ISSUER_DESTINATION_UNAVAILABLE",
    )
    mirror = run_check(qr_url="https://mirror.example/company/00123456", transport=transport())
    assert mirror.result is ea.Authenticity.NOT_PROVEN and mirror.reasons == (
        "QR_HOST_NOT_THE_ISSUERS",
    )


def test_no_marker_or_an_unlisted_issuer_is_not_proven_and_goes_to_a_person() -> None:
    assert run_check().reasons == ("NO_AUTHENTICITY_MARKER",)
    stranger = asyncio.run(
        xc.check(
            CONTENT,
            issuer_id="unknown-registry",
            issuers=it.parse(doc()),
            qr_url=URL,
            transport=transport(),
            now=NOW,
        )
    )
    assert stranger.result is ea.Authenticity.NOT_PROVEN and stranger.reasons == (
        "ISSUER_NOT_TRUSTED",
    )
    empty = asyncio.run(
        xc.check(
            CONTENT,
            issuer_id="uk-ch",
            issuers={},
            signature=KEY.sign(CONTENT),
            key_id="k1",
            algorithm="Ed25519",
            now=NOW,
        )
    )
    assert empty.reasons == ("ISSUER_NOT_TRUSTED",)  # the shipped, empty list trusts nothing
    assert NOW + timedelta(days=1) > NOW
