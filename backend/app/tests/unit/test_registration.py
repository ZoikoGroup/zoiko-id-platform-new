"""Person-first sign-up rules (ZID-UX-WF-013A): countries, email policy, tokens, states and routes, as pure decisions.
The database and HTTP behaviour is in tests/component/test_registration_pg.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.onboarding.application.registration import Env, route_country
from zid_platform.modules.onboarding.domain.registration import (
    COUNTRIES,
    MAX_CONFIRM_ATTEMPTS,
    MAX_RESENDS,
    TOKEN_TTL,
    Intent,
    Journey,
    RegistrationError,
    State,
    canonical_email,
    check_token,
    country_of,
    journey_of,
    locale_of,
    may_resend,
    new_token,
    request_shaped,
    route_for,
    token_hash,
)

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
TABLE = {"GB": ["uk-1", "local-1"], "DE": ["eu-1"], "US": ["us-1"]}


def _env(here: str = "uk-1", endpoints: dict[str, str] | None = None) -> Env:
    return Env("t", here, "2026-11-03", "test", None, endpoints or {})


def _intent(token: str | None = None, **kw) -> Intent:
    base: dict = {
        "state": State.VERIFICATION_SENT,
        "token_hash": token_hash(token) if token else None,
        "token_expires_at": NOW + TOKEN_TTL if token else None,
        "resend_count": 0,
        "last_sent_at": NOW - timedelta(minutes=5),
        "confirm_attempts": 0,
        "expires_at": NOW + timedelta(hours=24),
        "email_hash": "h" * 32,
    }
    base.update(kw)
    return Intent(**base)


# ---------------------------------------------------------------------------------------------- country


def test_the_country_is_what_the_person_chose_and_has_no_default() -> None:
    assert country_of(" gb ") == "GB" and country_of("de") == "DE"
    for bad in ("", "  ", "XX", "UK", "GBR", "G", "+44", None):
        with pytest.raises(RegistrationError) as e:
            country_of(bad)  # type: ignore[arg-type]
        assert e.value.code == "COUNTRY_INVALID"  # AC-07: never defaults to GB, UK rules or +44
    assert {"GB", "US", "DE", "BR", "IN", "JP", "NG", "AU"} <= COUNTRIES and len(COUNTRIES) > 240


def test_a_country_with_no_home_cell_is_unavailable_never_routed_to_a_default() -> None:
    assert route_country(_env("uk-1"), "GB", TABLE).state == "HERE"
    assert route_country(_env("local-1"), "GB", TABLE).state == "HERE"
    other = route_country(_env("uk-1", {"eu-1": "https://eu.example.com"}), "DE", TABLE)
    assert (other.state, other.cell, other.url) == ("OTHER_CELL", "eu-1", "https://eu.example.com")
    assert (
        route_country(_env("uk-1"), "DE", TABLE).url is None
    )  # a known cell with no endpoint is still not here
    for country in ("BR", "IN", "JP", "NG"):
        assert route_country(_env("uk-1"), country, TABLE).state == "UNAVAILABLE"  # honest, not GB


def test_the_shipped_routing_table_lists_only_cells_the_catalogue_knows() -> None:
    from zid_platform.modules.onboarding.application.registration import _routing_table
    from zid_platform.modules.tenant.ports import cell_catalog

    known = set(cell_catalog().cells)
    table = _routing_table()
    assert table["GB"][0] == "uk-1" and "BR" not in table
    assert {
        c for cells in table.values() for c in cells if c in {"uk-1", "local-1", "eu-1"}
    } <= known


# ----------------------------------------------------------------------------------------------- email


def test_the_approved_canonicalization_changes_nothing_it_was_not_approved_to_change() -> None:
    assert (
        canonical_email("  Jane.Doe+shop@Example.COM ") == "jane.doe+shop@example.com"
    )  # no +tag stripping, no dot removal
    assert canonical_email("j.d@gmail.com") != canonical_email(
        "jd@gmail.com"
    )  # two addresses stay two
    assert (
        canonical_email("user@BÜCHER.example") == "user@xn--bcher-kva.example"
    )  # the domain in its ASCII form
    assert canonical_email("ünï@example.org") == "ünï@example.org"  # a Unicode local part is kept
    assert canonical_email("A@b.co") == canonical_email("a@B.CO")


@pytest.mark.parametrize(
    "bad",
    [
        "", "no-at", "a@b", "a@@b.co", "a b@example.com", "a@exa mple.com", "a@example..com", "<a>@example.com",
        "a,b@example.com", 'a"@example.com', "a@-example.com", "a\n@example.com", "a@example.com\r\nBcc: x@y.co", "x" * 65 + "@example.com",
        "a@" + "b" * 250 + ".com",
    ],
)  # fmt: skip
def test_an_address_that_could_smuggle_a_header_or_a_second_recipient_is_refused(bad: str) -> None:
    with pytest.raises(RegistrationError) as e:
        canonical_email(bad)
    assert e.value.code == "EMAIL_INVALID"


def test_journeys_locales_and_shapes() -> None:
    assert (
        journey_of("personal") is Journey.PERSONAL
        and journey_of(" ORGANIZATION ") is Journey.ORGANIZATION
    )
    with pytest.raises(RegistrationError):
        journey_of("")  # the default can be unselected, the submission cannot
    with pytest.raises(RegistrationError):
        journey_of("OWNER")  # a role is never a journey
    assert locale_of("pt-BR") == "pt-BR" and locale_of("zh-Hant-TW") == "zh-Hant-TW"
    assert (
        locale_of("<script>") == "und" and locale_of(None) == "und" and locale_of("") == "und"
    )  # never a country
    assert (
        request_shaped("A" * 24) and not request_shaped("short") and not request_shaped("a b" * 8)
    )


# ----------------------------------------------------------------------------------------------- tokens


def test_a_token_is_stored_only_as_a_hash_and_redeems_once_in_its_window() -> None:
    token, digest = new_token()
    assert token_hash(token) == digest and token not in repr(digest) and len(token) >= 32
    other, _ = new_token()
    assert token != other
    i = _intent(token)
    assert check_token(i, token, NOW) is None
    assert check_token(i, other, NOW) == "TOKEN_MISMATCH"  # wrong audience: another request's link
    assert check_token(i, token, NOW + TOKEN_TTL) == "TOKEN_EXPIRED"  # time-limited
    assert (
        check_token(_intent(token, expires_at=NOW), token, NOW) == "REQUEST_EXPIRED"
    )  # stale intent
    assert (
        check_token(_intent(token, state=State.VERIFIED), token, NOW) == "REQUEST_NOT_OPEN"
    )  # replay after use
    assert check_token(_intent(token, state=State.COMPLETED), token, NOW) == "REQUEST_NOT_OPEN"
    assert check_token(_intent(token, state=State.RISK_HOLD), token, NOW) == "REQUEST_NOT_OPEN"
    assert check_token(_intent(token, state=State.EXPIRED), token, NOW) == "REQUEST_NOT_OPEN"
    assert check_token(_intent(None), token, NOW) == "TOKEN_EXPIRED"  # nothing was ever issued
    assert check_token(i, "short", NOW) == "TOKEN_MISMATCH"
    assert (
        check_token(_intent(token, confirm_attempts=MAX_CONFIRM_ATTEMPTS), token, NOW)
        == "TOO_MANY_ATTEMPTS"
    )


def test_resends_are_open_only_while_the_request_is_open_and_never_in_a_burst() -> None:
    assert may_resend(_intent("x" * 40), NOW)
    assert not may_resend(_intent("x" * 40, last_sent_at=NOW - timedelta(seconds=10)), NOW)
    assert not may_resend(_intent("x" * 40, resend_count=MAX_RESENDS), NOW)
    assert not may_resend(_intent("x" * 40, state=State.VERIFIED), NOW)
    assert not may_resend(_intent("x" * 40, state=State.RISK_HOLD), NOW)
    assert not may_resend(_intent("x" * 40, expires_at=NOW), NOW)


# ----------------------------------------------------------------------------------------------- routes


def test_an_account_is_active_but_never_identity_verified_and_holds_no_organization() -> None:
    """AC-01 and AC-02: whatever the journey, sign-up completes into an active account with no verified identity, and the organization
    journey ends in a provisional claim (13B), not a verified company or an Owner."""
    for journey in Journey:
        for enrolled in (False, True):
            r = route_for(
                State.VERIFIED, journey, authenticator_enrolled=enrolled, invitation=False
            )
            assert (r.account, r.identity, r.organization) == ("ACTIVE", "NOT_VERIFIED", "NONE")
    assert (
        route_for(
            State.VERIFIED, Journey.PERSONAL, authenticator_enrolled=False, invitation=False
        ).next_step
        == "ENROLL_AUTHENTICATOR"
    )
    assert (
        route_for(
            State.COMPLETED, Journey.PERSONAL, authenticator_enrolled=True, invitation=False
        ).next_step
        == "PERSONAL_WORKSPACE"
    )
    assert (
        route_for(
            State.COMPLETED, Journey.ORGANIZATION, authenticator_enrolled=True, invitation=False
        ).next_step
        == "ORGANIZATION_CLAIM"
    )
    # an organization claim waits for the authenticator ("only after account security")
    assert (
        route_for(
            State.VERIFIED, Journey.ORGANIZATION, authenticator_enrolled=False, invitation=False
        ).next_step
        == "ENROLL_AUTHENTICATOR"
    )
    # an invitation takes priority over a new company, and a person cannot choose a role
    assert (
        route_for(
            State.COMPLETED, Journey.ORGANIZATION, authenticator_enrolled=True, invitation=True
        ).next_step
        == "ACCEPT_INVITATION"
    )
    assert (
        route_for(
            State.VERIFIED, Journey.PERSONAL, authenticator_enrolled=False, invitation=True
        ).next_step
        == "ENROLL_AUTHENTICATOR"
    )


def test_before_the_inbox_is_proved_there_is_no_account_to_describe() -> None:
    for state in (State.INITIATED, State.VERIFICATION_SENT):
        r = route_for(state, Journey.PERSONAL, authenticator_enrolled=False, invitation=False)
        assert (r.next_step, r.account) == ("VERIFY_EMAIL", "NONE")
    assert (
        route_for(
            State.EXPIRED, Journey.PERSONAL, authenticator_enrolled=False, invitation=False
        ).next_step
        == "REQUEST_NEW_LINK"
    )
    held = route_for(
        State.RISK_HOLD, Journey.ORGANIZATION, authenticator_enrolled=False, invitation=False
    )
    assert (
        held.next_step == "CONTACT_SUPPORT"
        and held.account == "NONE"
        and held.identity == "NOT_VERIFIED"
    )


def test_registration_follows_the_country_registry_and_a_sandbox_never_counts_when_deployed() -> (
    None
):
    """ZID-EDR-001: account registration only in approved markets. The registry decides; unlisted countries are closed; a country always
    has somewhere to go."""
    from zid_platform.modules.onboarding.application.registration import _contact, registration_open

    local, deployed = (
        Env("t", "uk-1", "n", "local", None, {}),
        Env("t", "uk-1", "n", "production", None, {}),
    )
    for country in ("GB", "US", "IE", "DE", "FR"):  # the five wave 1 markets
        assert registration_open(local, country, NOW), country
        assert not registration_open(deployed, country, NOW), country  # a sandbox is for development only
    for country in ("BR", "IN", "ZZ"):
        assert not registration_open(local, country, NOW), (
            country
        )  # discovery is open, registration is not
    assert _contact("GB") == "support@zoikogroup.com" and _contact("BR") == "support@zoikogroup.com"
