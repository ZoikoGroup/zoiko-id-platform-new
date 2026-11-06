"""Revocation, offline bundle verification and the CRL fetcher for timestamp authorities (DEC-F2 §3).

A certificate revoked at or before the stamp time fails the timestamp; one revoked later does not (the stamp was made while it
was good). `required` mode needs a current CRL for every certificate below the root; `if_available` notes its absence.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from tsa_authority import DIGEST, NOW, Authority, authority, make_crl, response
from zid_crypto import LocalSigner, sign_jws
from zid_evidence import (
    CHECKPOINT_TYP,
    CheckpointManifest,
    LedgerEntry,
    batch_root,
    build_bundle,
    entry_hash,
    format_time,
    hash_json,
    merkle,
    verify_bundle,
)
from zid_evidence.__main__ import main as cli
from zid_evidence.bundle import TimestampOptions
from zid_evidence.checkpoint import entry_leaf
from zid_evidence.timestamp import (
    TimestampInvalid,
    check_revocation,
    crl_urls,
    load_crls,
    verify_timestamp_response,
)
from zid_platform.modules.evidence.infrastructure.crl import CrlFetcher, acceptable_url
from zid_platform.modules.evidence.infrastructure.tsa_token import (
    TimestampRejected,
    TimestampVerifier,
)

TSA_CRL = "http://crl.test/mid.crl"
MID_CRL = "http://crl.test/root.crl"


def chain_authority(**kw: Any) -> Authority:
    """root -> intermediate -> TSA, each with a CRL address."""
    return authority(intermediate=True, mid_crl_url=MID_CRL, crl_url=TSA_CRL, **kw)


def verified(auth: Authority) -> Any:
    return verify_timestamp_response(response(auth), DIGEST, [auth.root], nonce=1234, now=NOW)


def good_crls(auth: Authority, **kw: Any) -> list[x509.CertificateRevocationList]:
    """What the two issuers publish when nothing is revoked."""
    return [
        make_crl(auth.mid, auth.mid_key, **kw),  # type: ignore[arg-type]
        make_crl(auth.root, auth.root_key, **kw),
    ]


def rejects(auth: Authority, crls: list[Any], why: str, mode: str = "required") -> None:
    with pytest.raises(TimestampInvalid, match=why):
        check_revocation(verified(auth), crls, mode=mode, now=NOW)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------- revocation states


def test_nothing_revoked_is_good() -> None:
    auth = chain_authority()
    assert (
        check_revocation(verified(auth), good_crls(auth), mode="required", now=NOW).revocation
        == "good"
    )


def test_off_checks_nothing() -> None:
    auth = chain_authority()
    revoked = [make_crl(auth.mid, auth.mid_key, [(auth.tsa_cert, NOW - timedelta(days=1))])]
    assert (
        check_revocation(verified(auth), revoked, mode="off", now=NOW).revocation == "not_checked"
    )


def test_a_certificate_revoked_before_the_stamp_fails_the_timestamp() -> None:
    auth = chain_authority()
    crls = [
        make_crl(auth.mid, auth.mid_key, [(auth.tsa_cert, NOW - timedelta(hours=1))]),
        make_crl(auth.root, auth.root_key),
    ]
    rejects(auth, crls, "revoked at")


def test_an_intermediate_revoked_before_the_stamp_fails_the_timestamp() -> None:
    auth = chain_authority()
    crls = [
        make_crl(auth.mid, auth.mid_key),
        make_crl(auth.root, auth.root_key, [(auth.mid, NOW - timedelta(days=2))]),
    ]
    rejects(auth, crls, "Test Intermediate was revoked")


def test_a_certificate_revoked_after_the_stamp_does_not_undo_it_but_is_said() -> None:
    auth = chain_authority()
    later = NOW + timedelta(days=3)
    crls = [
        make_crl(
            auth.mid, auth.mid_key, [(auth.tsa_cert, later)], last_update=later + timedelta(hours=1)
        ),
        make_crl(auth.root, auth.root_key, last_update=later + timedelta(hours=1)),
    ]
    got = check_revocation(verified(auth), crls, mode="required", now=later + timedelta(days=1))
    assert got.revocation == "revoked_later"


def test_an_invalidity_date_before_the_stamp_wins_over_a_later_revocation_date() -> None:
    """The CA says the key was bad from `invalidity`, though it published the revocation afterwards."""
    auth = chain_authority()
    published = NOW + timedelta(days=3)
    crls = [
        make_crl(
            auth.mid,
            auth.mid_key,
            [(auth.tsa_cert, published)],
            last_update=published,
            invalidity={auth.tsa_cert.serial_number: NOW - timedelta(days=1)},
        ),
        make_crl(auth.root, auth.root_key, last_update=published),
    ]
    with pytest.raises(TimestampInvalid, match="revoked at"):
        check_revocation(verified(auth), crls, mode="required", now=published)


def test_required_needs_a_current_list_for_every_certificate_below_the_root() -> None:
    auth = chain_authority()
    rejects(auth, [], "no current revocation list")
    rejects(
        auth, [make_crl(auth.mid, auth.mid_key)], "no current revocation list"
    )  # the intermediate has none


def test_if_available_notes_a_missing_list_and_still_catches_a_revocation() -> None:
    auth = chain_authority()
    assert (
        check_revocation(verified(auth), [], mode="if_available", now=NOW).revocation == "partial"
    )
    revoked = [make_crl(auth.mid, auth.mid_key, [(auth.tsa_cert, NOW - timedelta(hours=1))])]
    rejects(auth, revoked, "revoked at", mode="if_available")


def test_a_list_published_before_the_stamp_is_used_and_says_how_old_it_is() -> None:
    """Real CRLs are periodic (found against DigiCert and Sectigo): the one in force predates the stamp."""
    auth = chain_authority()
    old = NOW - timedelta(days=1)
    crls = good_crls(auth, last_update=old, next_update=NOW + timedelta(days=5))
    got = check_revocation(verified(auth), crls, mode="required", now=NOW)
    assert got.revocation == "good"
    assert got.revocation_as_of == old and got.revocation_as_of < got.gen_time


def test_a_list_dated_in_the_future_is_not_trusted() -> None:
    auth = chain_authority()
    future = NOW + timedelta(days=1)
    rejects(
        auth,
        good_crls(auth, last_update=future, next_update=future + timedelta(days=7)),
        "no current revocation list",
    )


def test_an_expired_list_is_not_current() -> None:
    auth = chain_authority()
    expired = good_crls(auth, last_update=NOW, next_update=NOW + timedelta(hours=1))
    with pytest.raises(TimestampInvalid, match="no current revocation list"):
        check_revocation(verified(auth), expired, mode="required", now=NOW + timedelta(days=2))


def test_a_list_not_signed_by_the_issuer_is_ignored() -> None:
    auth = chain_authority()
    forger = ec.generate_private_key(ec.SECP256R1())
    forged = [
        make_crl(auth.mid, forger),
        make_crl(auth.root, auth.root_key),
    ]  # claims to be the intermediate's
    rejects(auth, forged, "no current revocation list")


def test_a_list_from_another_issuer_is_ignored() -> None:
    auth, other = chain_authority(), chain_authority()
    wrong = [make_crl(other.mid, other.mid_key), make_crl(other.root, other.root_key)]
    rejects(auth, wrong, "no current revocation list")


def test_a_tsa_certificate_issued_straight_by_the_root_is_checked_against_the_root_list() -> None:
    auth = authority(crl_url=MID_CRL)
    crls = [make_crl(auth.root, auth.root_key, [(auth.tsa_cert, NOW - timedelta(hours=1))])]
    rejects(auth, crls, "revoked at")
    assert (
        check_revocation(
            verified(auth), [make_crl(auth.root, auth.root_key)], mode="required", now=NOW
        ).revocation
        == "good"
    )


def test_crl_addresses_are_read_from_certificates_and_only_http() -> None:
    auth = chain_authority()
    assert crl_urls(auth.tsa_cert) == [TSA_CRL]
    assert crl_urls(auth.mid) == [MID_CRL]
    assert crl_urls(auth.root) == []


def test_crls_load_from_pem_and_der() -> None:
    auth = chain_authority()
    a, b = good_crls(auth)
    pem = a.public_bytes(serialization.Encoding.PEM) + b.public_bytes(serialization.Encoding.PEM)
    assert len(load_crls(pem)) == 2
    assert len(load_crls(a.public_bytes(serialization.Encoding.DER))) == 1
    with pytest.raises(ValueError):
        load_crls(b"garbage")


# ----------------------------------------------------------------------------------------------- the async verifier


class _Source:
    def __init__(self, crls: list[Any]) -> None:
        self.crls, self.asked = crls, []

    async def fetch(self, urls: Any) -> list[Any]:
        self.asked.append(list(urls))
        return self.crls


def test_the_verifier_asks_only_for_the_chains_own_lists_and_never_for_the_root() -> None:
    auth = chain_authority()
    source = _Source(good_crls(auth))
    v = TimestampVerifier([auth.root], revocation="required", crls=source)
    got = asyncio.run(v.verify_response(response(auth), DIGEST, nonce=1234, now=NOW))
    assert got.revocation == "good"
    assert source.asked == [[TSA_CRL, MID_CRL]]


def test_the_verifier_turns_a_revocation_into_a_rejection() -> None:
    auth = chain_authority()
    revoked = [
        make_crl(auth.mid, auth.mid_key, [(auth.tsa_cert, NOW - timedelta(hours=1))])
    ] + good_crls(auth)[1:]
    v = TimestampVerifier([auth.root], revocation="required", crls=_Source(revoked))
    with pytest.raises(TimestampRejected, match="revoked at"):
        asyncio.run(v.verify_response(response(auth), DIGEST, nonce=1234, now=NOW))


def test_required_fails_when_lists_cannot_be_fetched_and_if_available_does_not() -> None:
    auth = chain_authority()
    strict = TimestampVerifier([auth.root], revocation="required", crls=_Source([]))
    with pytest.raises(TimestampRejected, match="no current revocation list"):
        asyncio.run(strict.verify_response(response(auth), DIGEST, nonce=1234, now=NOW))
    lenient = TimestampVerifier([auth.root], revocation="if_available", crls=_Source([]))
    assert (
        asyncio.run(lenient.verify_response(response(auth), DIGEST, nonce=1234, now=NOW)).revocation
        == "partial"
    )


def test_the_verifier_does_not_fetch_for_a_token_that_does_not_verify() -> None:
    auth, rogue = chain_authority(), chain_authority()
    source = _Source([])
    v = TimestampVerifier([auth.root], revocation="required", crls=source)
    with pytest.raises(TimestampRejected, match="does not chain"):
        asyncio.run(v.verify_response(response(rogue), DIGEST, nonce=1234, now=NOW))
    assert source.asked == []  # an address from an untrusted certificate is never followed


# ----------------------------------------------------------------------------------------------- the fetcher


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://crl.example.com/a.crl", True),
        ("https://crl.example.com/a.crl", True),
        ("ftp://crl.example.com/a.crl", False),
        ("file:///etc/passwd", False),
        ("http://localhost/a.crl", False),
        ("http://127.0.0.1/a.crl", False),
        ("http://10.0.0.5/a.crl", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("http://[::1]/a.crl", False),
        ("not a url", False),
    ],
)
def test_only_public_http_addresses_are_followed(url: str, ok: bool) -> None:
    assert acceptable_url(url) is ok


class _FakeHttp:
    pages: dict[str, Any] = {}  # noqa: RUF012 - set per test
    calls: list[str] = []  # noqa: RUF012

    def __init__(self, **_: object) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None: ...

    async def get(self, url: str) -> object:
        type(self).calls.append(url)
        status, body = type(self).pages[url]
        return SimpleNamespace(status_code=status, content=body)


def test_the_fetcher_caches_skips_failures_and_refuses_unsafe_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    auth = chain_authority()
    der = good_crls(auth)[0].public_bytes(serialization.Encoding.DER)
    fake = type(
        "Fake", (_FakeHttp,), {"pages": {TSA_CRL: (200, der), MID_CRL: (500, b"")}, "calls": []}
    )
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    f = CrlFetcher(ttl=timedelta(hours=1))
    urls = [TSA_CRL, MID_CRL, "http://127.0.0.1/x.crl", TSA_CRL]
    got = asyncio.run(f.fetch(urls))
    assert len(got) == 1 and fake.calls == [
        TSA_CRL,
        MID_CRL,
    ]  # the loopback address was never requested, the duplicate once
    asyncio.run(f.fetch([TSA_CRL]))
    assert fake.calls == [TSA_CRL, MID_CRL]  # served from cache


def test_the_fetcher_ignores_an_oversized_or_unparseable_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    fake = type(
        "Fake",
        (_FakeHttp,),
        {"pages": {TSA_CRL: (200, b"x" * 100), MID_CRL: (200, b"not a crl")}, "calls": []},
    )
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    assert asyncio.run(CrlFetcher(max_bytes=50).fetch([TSA_CRL, MID_CRL])) == []


# ----------------------------------------------------------------------------------------------- offline bundles

ALG = "sha-256"
T0 = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)


def anchored_bundle(
    auth: Authority | None, *, anchor: bool = True
) -> tuple[dict[str, Any], LocalSigner]:
    """A one-entry bundle whose checkpoint is stamped by `auth` (or carries no timestamp)."""
    signer = LocalSigner("evidence-k1")
    record = {"receipt_id": "r1", "outcome": "ALLOW"}
    entry = LedgerEntry(
        partition_id="tenant:t1:uk-1",
        sequence_no=1,
        record_id="r1",
        record_type="decision_receipt",
        record_hash=hash_json(record, alg=ALG),
        previous_entry_hash=None,
        committed_at=format_time(T0),
        evidence_class="E3",
    )
    digest = entry_hash(entry, alg=ALG)
    manifest = CheckpointManifest(
        checkpoint_id="cp-1",
        partition_id=entry.partition_id,
        first_sequence=1,
        last_sequence=1,
        merkle_root=batch_root([digest], alg=ALG),
        previous_checkpoint_hash=None,
        created_at=format_time(NOW),
        signer_key_id=signer.kid,
        alg=signer.alg,
    )
    ref = None
    if anchor and auth is not None:
        import base64

        stamped = manifest.hash(alg=manifest.merkle_root.alg)
        ref = "rfc3161:" + base64.b64encode(response(auth, stamped)).decode()
    bundle = build_bundle(
        record_type=entry.record_type,
        record_id=entry.record_id,
        record=record,
        entry=entry,
        entry_digest=digest,
        manifest=manifest,
        checkpoint_signature=sign_jws(signer, manifest.to_dict(), typ=CHECKPOINT_TYP),
        proof=merkle.inclusion_proof(0, [entry_leaf(digest)], alg=ALG),
        keys=[signer.public_jwk()],
        external_timestamp_ref=ref,
    )
    return json.loads(json.dumps(bundle)), signer


def check(report: Any) -> dict[str, Any]:
    return next(c for c in report.to_dict()["checks"] if c["name"] == "external_timestamp")


def test_a_bundle_with_a_proven_timestamp_verifies_offline() -> None:
    auth = chain_authority()
    bundle, signer = anchored_bundle(auth)
    options = TimestampOptions(
        roots=[auth.root], crls=good_crls(auth), revocation="required", now=NOW
    )
    report = verify_bundle(bundle, trusted_keys=[signer.public_jwk()], timestamps=options)
    assert report.ok, report.to_dict()
    assert "revocation good" in check(report)["detail"]


def test_a_timestamp_nobody_checked_is_reported_as_not_verified_not_as_proof() -> None:
    auth = authority()
    bundle, signer = anchored_bundle(auth)
    report = verify_bundle(bundle, trusted_keys=[signer.public_jwk()])
    assert report.ok and "not verified" in check(report)["detail"]
    required = verify_bundle(
        bundle, trusted_keys=[signer.public_jwk()], timestamps=TimestampOptions(required=True)
    )
    assert not required.ok


def test_a_timestamp_over_a_different_checkpoint_fails_the_bundle() -> None:
    auth = authority()
    bundle, signer = anchored_bundle(auth)
    other, _ = anchored_bundle(auth)
    other_manifest_ref = other["checkpoint"]["external_timestamp_ref"]
    bundle["checkpoint"]["created_at"] = format_time(
        NOW + timedelta(seconds=1)
    )  # a different manifest now
    bundle["checkpoint"]["external_timestamp_ref"] = other_manifest_ref
    report = verify_bundle(
        bundle,
        trusted_keys=[signer.public_jwk()],
        timestamps=TimestampOptions(roots=[auth.root], now=NOW),
    )
    assert (
        not report.ok
    )  # the signature no longer covers it, and the timestamp is for another manifest


def test_an_untrusted_or_revoked_timestamp_fails_the_bundle() -> None:
    auth, stranger = chain_authority(), chain_authority()
    bundle, signer = anchored_bundle(auth)
    keys = [signer.public_jwk()]
    untrusted = verify_bundle(
        bundle, trusted_keys=keys, timestamps=TimestampOptions(roots=[stranger.root], now=NOW)
    )
    assert not untrusted.ok and "does not chain" in check(untrusted)["detail"]
    revoked = [
        make_crl(auth.mid, auth.mid_key, [(auth.tsa_cert, NOW - timedelta(hours=1))]),
        make_crl(auth.root, auth.root_key),
    ]
    gone = verify_bundle(
        bundle,
        trusted_keys=keys,
        timestamps=TimestampOptions(roots=[auth.root], crls=revoked, now=NOW),
    )
    assert not gone.ok and "revoked at" in check(gone)["detail"]


def test_an_unanchored_checkpoint_passes_unless_an_anchor_is_required() -> None:
    bundle, signer = anchored_bundle(None, anchor=False)
    keys = [signer.public_jwk()]
    assert verify_bundle(bundle, trusted_keys=keys).ok
    auth = authority()
    assert verify_bundle(
        bundle, trusted_keys=keys, timestamps=TimestampOptions(roots=[auth.root])
    ).ok
    strict = verify_bundle(
        bundle, trusted_keys=keys, timestamps=TimestampOptions(roots=[auth.root], required=True)
    )
    assert not strict.ok and "not anchored" in check(strict)["detail"]


def test_the_command_line_verifies_a_timestamp_with_roots_and_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    auth = chain_authority()
    bundle, signer = anchored_bundle(auth)
    files = {
        "bundle.json": json.dumps(bundle).encode(),
        "keys.json": json.dumps({"keys": [signer.public_jwk()]}).encode(),
        "roots.pem": auth.root.public_bytes(serialization.Encoding.PEM),
        "mid.crl": good_crls(auth)[0].public_bytes(serialization.Encoding.PEM),
        "root.crl": good_crls(auth)[1].public_bytes(serialization.Encoding.DER),
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    argv = ["verify", str(tmp_path / "bundle.json"), "--keys", str(tmp_path / "keys.json")]
    stamp = [
        "--tsa-roots",
        str(tmp_path / "roots.pem"),
        "--tsa-crl",
        str(tmp_path / "mid.crl"),
        "--tsa-crl",
        str(tmp_path / "root.crl"),
    ]
    assert cli([*argv, *stamp, "--tsa-revocation", "required"]) == 0, capsys.readouterr().out
    only_one = ["--tsa-roots", str(tmp_path / "roots.pem"), "--tsa-crl", str(tmp_path / "mid.crl")]
    capsys.readouterr()
    assert (
        cli([*argv, *only_one, "--tsa-revocation", "required"]) == 1
    )  # the intermediate's list is missing
    assert "no current revocation list" in capsys.readouterr().out
    assert cli([*argv, *only_one, "--tsa-revocation", "if_available"]) == 0
    assert cli([*argv, *stamp, "--tsa-revocation", "off"]) == 0
    assert (
        cli([*argv, "--require-timestamp"]) == 1
    )  # a timestamp present but given no trust list is not enough when required
    assert cli([*argv, "--tsa-roots", str(tmp_path / "missing.pem")]) == 2


def test_receipt_verification_reports_the_timestamp_it_proved_or_could_not() -> None:
    from zid_platform.modules.evidence.application.replay import _timestamp_check

    auth = authority()
    bundle, _ = anchored_bundle(auth)
    proved = asyncio.run(_timestamp_check(bundle, TimestampVerifier([auth.root], revocation="off")))
    assert proved is not None and proved["ok"] and "verified against" in proved["detail"]
    unchecked = asyncio.run(_timestamp_check(bundle, None))
    assert unchecked is not None and unchecked["ok"] and "not verified" in unchecked["detail"]
    refused = asyncio.run(
        _timestamp_check(bundle, TimestampVerifier([authority().root], revocation="off"))
    )
    assert refused is not None and not refused["ok"] and "does not chain" in refused["detail"]
    unanchored, _ = anchored_bundle(None, anchor=False)
    assert (
        asyncio.run(_timestamp_check(unanchored, TimestampVerifier([auth.root], revocation="off")))
        is None
    )
