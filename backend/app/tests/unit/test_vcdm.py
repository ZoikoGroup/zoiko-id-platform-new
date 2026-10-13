"""W3C VCDM 2.0 rules (ZID-VC-001 §9, §13; VC-JOSE-COSE; Bitstring Status List v1.0)."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_platform.modules.credential.domain.credentials import CredentialError, b64e
from zid_platform.modules.credential.domain.vcdm import (
    BASE_CONTEXT,
    bit_at,
    check_credential,
    encode_list,
    enveloped_credential,
    status_entries,
    status_list_bits,
)

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
TYPE = "ExampleAgeCredential"


def vc(**kw: Any) -> dict[str, Any]:
    return {
        "@context": [BASE_CONTEXT],
        "type": ["VerifiableCredential", TYPE],
        "issuer": "https://issuer.example",
        "validFrom": "2026-01-01T00:00:00Z",
        "validUntil": "2027-01-01T00:00:00Z",
        "credentialSubject": {"id": "did:example:holder", "age_over_18": True},
        **kw,
    }


def code(fn: Any, *args: Any, **kw: Any) -> str:
    with pytest.raises(CredentialError) as e:
        fn(*args, **kw)
    return e.value.code


def test_credential_rules() -> None:
    assert check_credential(vc(), credential_type=TYPE, now=NOW) == {"age_over_18": True}
    assert code(check_credential, vc(), credential_type="Other", now=NOW) == (
        "CREDENTIAL_TYPE_MISMATCH"
    )
    expired = vc(validUntil="2026-10-01T00:00:00Z")
    assert code(check_credential, expired, credential_type=TYPE, now=NOW) == "CREDENTIAL_EXPIRED"
    future = vc(validFrom="2026-12-01T00:00:00Z")
    assert code(check_credential, future, credential_type=TYPE, now=NOW) == (
        "CREDENTIAL_NOT_YET_VALID"
    )
    naive = vc(validUntil="2027-01-01T00:00:00")
    assert code(check_credential, naive, credential_type=TYPE, now=NOW) == "VC_MALFORMED"
    two_subjects = vc(credentialSubject=[{"a": 1}, {"b": 2}])
    assert code(check_credential, two_subjects, credential_type=TYPE, now=NOW) == "VC_MALFORMED"


def test_contexts_are_pinned_never_fetched_or_inlined() -> None:
    for contexts in (
        ["https://www.w3.org/2018/credentials/v1"],  # VCDM 1.1 base
        [BASE_CONTEXT, "https://evil.example/context"],
        [BASE_CONTEXT, {"age_over_18": "https://evil.example/redefined"}],
        "https://www.w3.org/ns/credentials/v2",
    ):
        doc = vc(**{"@context": contexts})
        assert code(check_credential, doc, credential_type=TYPE, now=NOW) == (
            "VC_CONTEXT_NOT_ALLOWED"
        )


def test_enveloped_credential() -> None:
    vp = {
        "@context": [BASE_CONTEXT],
        "type": ["VerifiablePresentation"],
        "verifiableCredential": [
            {
                "@context": [BASE_CONTEXT],
                "type": "EnvelopedVerifiableCredential",
                "id": "data:application/vc+jwt,eyJ.a.b",
            }
        ],
    }
    assert enveloped_credential(vp) == "eyJ.a.b"
    two = {**vp, "verifiableCredential": vp["verifiableCredential"] * 2}
    assert code(enveloped_credential, two) == "VP_MALFORMED"
    embedded = {**vp, "verifiableCredential": [vc()]}  # unsecured embedded VC: never accepted
    assert code(enveloped_credential, embedded) == "VP_MALFORMED"


def test_bitstring_is_msb_first_and_bounded() -> None:
    bits = bytearray(16 * 1024)
    bits[0] = 0b0100_0000  # index 1
    bits[1] = 0b0000_0001  # index 15
    encoded = encode_list(bytes(bits))
    status = {
        "@context": [BASE_CONTEXT],
        "id": "https://issuer.example/status/1",
        "type": ["VerifiableCredential", "BitstringStatusListCredential"],
        "issuer": "https://issuer.example",
        "credentialSubject": {
            "type": "BitstringStatusList",
            "statusPurpose": "revocation",
            "encodedList": encoded,
        },
    }
    raw = status_list_bits(
        status,
        issuer="https://issuer.example",
        url="https://issuer.example/status/1",
        purpose="revocation",
        now=NOW,
    )
    assert [bit_at(raw, i) for i in (0, 1, 2, 7, 8, 15)] == [0, 1, 0, 0, 0, 1]
    assert code(bit_at, raw, len(raw) * 8) == "STATUS_LIST_INDEX_OUT_OF_RANGE"
    for bad in (
        {"issuer": "https://other.example"},
        {"id": "https://issuer.example/status/2"},
        {"credentialSubject": {**status["credentialSubject"], "statusPurpose": "suspension"}},
        {"credentialSubject": {**status["credentialSubject"], "encodedList": encoded[1:]}},
    ):
        assert (
            code(
                status_list_bits,
                {**status, **bad},
                issuer="https://issuer.example",
                url="https://issuer.example/status/1",
                purpose="revocation",
                now=NOW,
            )
            == "STATUS_LIST_INVALID"
        )
    bomb = "u" + b64e(gzip.compress(bytes(64 * 1024 * 1024)))  # 64 MiB of zeros
    with pytest.raises(CredentialError):
        status_list_bits(
            {**status, "credentialSubject": {**status["credentialSubject"], "encodedList": bomb}},
            issuer="https://issuer.example",
            url="https://issuer.example/status/1",
            purpose="revocation",
            now=NOW + timedelta(0),
        )


def test_status_entries() -> None:
    entry = {
        "type": "BitstringStatusListEntry",
        "statusPurpose": "revocation",
        "statusListIndex": "94567",
        "statusListCredential": "https://issuer.example/status/1",
    }
    assert status_entries(vc(credentialStatus=entry))[0].index == 94567
    assert status_entries(vc(credentialStatus=[{"type": "SomethingElse"}])) == []
    assert status_entries(vc()) == []
    bad = {**entry, "statusListIndex": "-1"}
    assert code(status_entries, vc(credentialStatus=bad)) == "VC_MALFORMED"
