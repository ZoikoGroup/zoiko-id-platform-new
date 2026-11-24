"""The Verify API's pure rules (CR-149): names, environments, scopes, origins, secrets and the failed-attempt limit."""

from __future__ import annotations

import pytest
from zid_platform.modules.verify_api.application.throttle import FailureLimiter
from zid_platform.modules.verify_api.domain import model as m


def test_an_application_name_is_plain_and_cleaned() -> None:
    assert m.check_name("  Checkout   web ") == "Checkout web"
    for bad in ("ab", "x" * 81, "-start", "end-", "has/slash", "tab\tinside!", ""):
        with pytest.raises(m.PartnerError) as e:
            m.check_name(bad)
        assert e.value.code == "VALIDATION_FAILED", bad


def test_only_the_sandbox_can_be_created_until_the_production_gate_has_passed() -> None:
    assert m.check_environment("sandbox") is m.Environment.SANDBOX
    with pytest.raises(m.PartnerError) as e:
        m.check_environment("PRODUCTION")
    assert e.value.code == "PRODUCTION_NOT_APPROVED"
    with pytest.raises(m.PartnerError) as e2:
        m.check_environment("staging")
    assert e2.value.code == "VALIDATION_FAILED"


def test_scopes_only_narrow() -> None:
    assert m.check_scopes(None) == m.DEFAULT_SANDBOX_SCOPES
    assert (
        "applications:submit" not in m.DEFAULT_SANDBOX_SCOPES
    )  # a sandbox application does not ask for production by default
    assert m.check_scopes(["verifications:create", "verifications:create", " decisions:read "]) == (
        "verifications:create",
        "decisions:read",
    )
    held = ("verifications:create", "decisions:read")
    assert m.check_scopes(["decisions:read"], held) == ("decisions:read",)
    assert m.check_scopes(None, held) == held  # no request: what it holds, never more
    with pytest.raises(m.PartnerError) as e:
        m.check_scopes(["usage:read"], held)
    assert e.value.code == "INVALID_SCOPE"
    for bad, code in (
        ([], "VALIDATION_FAILED"),
        (["nonsense"], "VALIDATION_FAILED"),
        ([" "], "VALIDATION_FAILED"),
    ):
        with pytest.raises(m.PartnerError) as e2:
            m.check_scopes(bad, held)
        assert e2.value.code == code, bad


def test_origins_are_exact_https_origins() -> None:
    assert m.check_origins(
        ["https://app.example.com", "https://APP.example.com/", "http://localhost:3000"]
    ) == (
        "https://app.example.com",
        "http://localhost:3000",
    )
    assert m.check_origins(None) == ()
    for bad in (
        "https://*.example.com",
        "http://example.com",  # plain http only for a local development origin
        "https://example.com/path",
        "https://example.com?x=1",
        "https://example.com#frag",
        "https://user@example.com",
        "ftp://example.com",
        "example.com",
        "",
    ):
        with pytest.raises(m.PartnerError):
            m.check_origins([bad])
    with pytest.raises(m.PartnerError):
        m.check_origins([f"https://h{i}.example.com" for i in range(m.MAX_REDIRECT_ORIGINS + 1)])


def test_an_application_moves_only_along_allowed_steps_and_a_retired_one_stays_retired() -> None:
    A = m.AppStatus
    assert m.check_app_transition(A.ACTIVE, A.SUSPENDED) is A.SUSPENDED
    assert m.check_app_transition(A.SUSPENDED, A.ACTIVE) is A.ACTIVE
    assert m.check_app_transition(A.SUSPENDED, A.RETIRED) is A.RETIRED
    for current, target in ((A.RETIRED, A.ACTIVE), (A.RETIRED, A.SUSPENDED), (A.ACTIVE, A.ACTIVE)):
        with pytest.raises(m.PartnerError) as e:
            m.check_app_transition(current, target)
        assert e.value.code == "APPLICATION_STATE_INVALID"


def test_secrets_are_long_random_prefixed_and_only_ever_compared_by_hash() -> None:
    cid, sec, tok = m.new_client_id(), m.new_secret(), m.new_token()
    assert (
        cid.startswith("zpc_")
        and len(cid) == 24
        and sec.startswith("zps_")
        and tok.startswith("zpt_")
    )
    assert len({m.new_secret() for _ in range(50)}) == 50  # not guessable and not repeated
    assert len(sec) >= 40  # about 256 bits
    h = m.hash_secret(sec)
    assert len(h) == 32 and sec.encode() not in h
    assert (
        m.secret_matches(sec, h)
        and not m.secret_matches(sec + "x", h)
        and not m.secret_matches("", h)
    )
    assert m.secret_hint(sec) == sec[-4:] and len(m.secret_hint(sec)) == 4
    assert sec not in m.mask(sec) and m.mask("short") == "…"


def test_too_many_wrong_secrets_for_one_client_are_refused_for_a_while_and_a_good_one_clears_the_count() -> (
    None
):
    lim = FailureLimiter(window=100, limit=3)
    assert lim.blocked("c1", now=0) == 0
    for t in (1, 2, 3):
        lim.failed("c1", now=t)
    wait = lim.blocked("c1", now=4)
    assert 90 <= wait <= 100
    assert lim.blocked("other", now=4) == 0  # another client is not slowed
    assert lim.blocked("c1", now=102) == 0  # the window has passed
    lim.failed("c2", now=0)
    lim.failed("c2", now=1)
    lim.succeeded("c2")
    lim.failed("c2", now=2)
    assert lim.blocked("c2", now=3) == 0


def test_the_limiter_forgets_the_oldest_clients_rather_than_growing_without_end() -> None:
    lim = FailureLimiter(window=100, limit=1, keys=3)
    for i in range(10):
        lim.failed(f"k{i}", now=float(i))
    assert len(lim._seen) == 3
    assert lim.blocked("k0", now=11) == 0  # forgotten
    assert lim.blocked("k9", now=11) > 0
