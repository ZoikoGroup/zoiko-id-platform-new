"""Runtime attestation statements (CR-042; MACHINE-001 §9): only an approved attestor's signature counts, and an
embedded or remote key is never trusted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_crypto import LocalSigner, sign_jws
from zid_platform.modules.machine.application import attestation as att
from zid_platform.modules.machine.domain.agents import MachineError

TENANT = "11111111-1111-4111-8111-111111111111"
SUB = f"spiffe://uk-1.test.id.zoiko/t/{TENANT}/ns/payments/sa/agent"
DIGEST = "sha256:" + "ab" * 32
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

ATTESTOR = LocalSigner("attestor-1", "Ed25519")
KEYS = [ATTESTOR.public_jwk()]


def claims(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sub": SUB,
        "environment_id": "prod",
        "artifact_digest": DIGEST,
        "level": 2,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=30)).timestamp()),
    }
    return base | over


def signed(
    payload: dict[str, object], *, signer: LocalSigner = ATTESTOR, typ: str = att.STATEMENT_TYP
) -> str:
    return sign_jws(signer, payload, typ=typ)


def read(token: str) -> att.Statement:
    header, body = att.signature_checked(token, KEYS)
    return att.read_claims(header, body)


def test_a_statement_signed_by_an_approved_attestor_is_read_with_its_tenant_from_the_spiffe_id() -> (
    None
):
    s = read(signed(claims()))
    assert (s.spiffe_id, s.tenant_id, s.environment_id, s.level, s.verifier) == (
        SUB,
        TENANT,
        "prod",
        2,
        "attestor-1",
    )
    assert att.timing_problem(s, NOW) is None
    assert len(s.commitment) == 64


def test_a_statement_from_anyone_else_is_refused_before_its_claims_are_read() -> None:
    stranger = LocalSigner("attestor-1", "Ed25519")  # same name, different key
    with pytest.raises(MachineError) as e:
        read(signed(claims(), signer=stranger))
    assert e.value.code == "ATTESTATION_SIGNATURE_INVALID"
    unknown = LocalSigner("attestor-9", "Ed25519")
    with pytest.raises(MachineError) as e2:
        read(signed(claims(), signer=unknown))
    assert e2.value.code == "ATTESTATION_SIGNATURE_INVALID"


def test_the_wrong_token_type_is_refused() -> None:
    with pytest.raises(MachineError) as e:
        read(signed(claims(), typ="JWT"))
    assert e.value.code == "ATTESTATION_SIGNATURE_INVALID"


@pytest.mark.parametrize(
    "over",
    [
        {"level": 0},
        {"level": 4},
        {"level": "high"},
        {"artifact_digest": "sha256:short"},
        {"artifact_digest": "md5:" + "ab" * 16},
        {"sub": "not a spiffe id"},
        {"sub": "spiffe://uk-1.test.id.zoiko/ns/no-tenant"},
        {"iat": "yesterday"},
    ],
)
def test_claims_that_cannot_be_read_are_malformed(over: dict[str, object]) -> None:
    with pytest.raises(MachineError) as e:
        read(signed(claims(**over)))
    assert e.value.code == "ATTESTATION_MALFORMED"


def test_a_statement_must_be_in_date_and_short_lived() -> None:
    past = read(
        signed(
            claims(
                iat=int((NOW - timedelta(hours=2)).timestamp()),
                exp=int((NOW - timedelta(hours=1)).timestamp()),
            )
        )
    )
    assert att.timing_problem(past, NOW) == "ATTESTATION_STALE"
    long = read(signed(claims(exp=int((NOW + timedelta(hours=3)).timestamp()))))
    assert att.timing_problem(long, NOW) == "ATTESTATION_LIFETIME_INVALID"
    backwards = read(
        signed(claims(exp=int((NOW - timedelta(minutes=5)).timestamp()), iat=int(NOW.timestamp())))
    )
    assert att.timing_problem(backwards, NOW) in {
        "ATTESTATION_STALE",
        "ATTESTATION_LIFETIME_INVALID",
    }
    future = read(
        signed(
            claims(
                iat=int((NOW + timedelta(minutes=10)).timestamp()),
                exp=int((NOW + timedelta(minutes=40)).timestamp()),
            )
        )
    )
    assert att.timing_problem(future, NOW) == "ATTESTATION_NOT_YET_VALID"


def test_an_attestor_may_only_vouch_up_to_its_ceiling() -> None:
    plain = ATTESTOR.public_jwk()  # no ceiling stated: level 2 at most
    hardware = {**ATTESTOR.public_jwk(), "zid_max_level": 3}
    low = {**ATTESTOR.public_jwk(), "zid_max_level": 1}
    two, three = read(signed(claims(level=2))), read(signed(claims(level=3)))
    assert (
        att.level_problem(two, [plain]) is None
        and att.level_problem(two, [low]) == "ATTESTATION_LEVEL_NOT_PERMITTED"
    )
    assert att.level_problem(three, [plain]) == "ATTESTATION_LEVEL_NOT_PERMITTED"
    assert att.level_problem(three, [hardware]) is None
    assert (
        att.level_problem(three, []) == "ATTESTATION_LEVEL_NOT_PERMITTED"
    )  # an unknown signer vouches for nothing


def test_the_commitment_changes_when_any_claim_changes() -> None:
    a = read(signed(claims()))
    assert read(signed(claims(level=3))).commitment != a.commitment
    assert read(signed(claims(environment_id="staging"))).commitment != a.commitment
    assert read(signed(claims())).commitment == a.commitment
