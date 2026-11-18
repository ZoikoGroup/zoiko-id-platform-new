"""WF-05 contacts and one-time challenges (ZID-DTV-WF-05 A02-A04, §22; QA-001 to QA-016; CR-114). Synthetic only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain import contact_challenges as ch
from zid_platform.modules.verification.infrastructure import phone_numbers as pn

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
KEY = b"k" * 32


def issue(channel: ch.Channel = ch.Channel.SMS) -> tuple[ch.Challenge, str]:
    return ch.issue(
        challenge_id="c-1",
        contact_id="p-1",
        contact_revision=1,
        channel=channel,
        session_ref="s-1",
        key=KEY,
        now=NOW,
    )


def confirm(c: ch.Challenge, code: str, **kw: object) -> ch.Outcome:
    args: dict[str, object] = {
        "key": KEY,
        "contact_revision": 1,
        "session_ref": "s-1",
        "now": NOW + timedelta(minutes=1),
    }
    args.update(kw)
    return ch.confirm(c, code, **args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "region", "e164", "calling"),
    [
        ("+1 415 555 2671", None, "+14155552671", "1"),
        ("020 7946 0958", "GB", "+442079460958", "44"),
        ("+91 98765 43210", None, "+919876543210", "91"),
        ("+971 50 123 4567", None, "+971501234567", "971"),
        ("+55 11 91234 5678", None, "+5511912345678", "55"),
        ("090-1234-5678", "JP", "+819012345678", "81"),
    ],
)
def test_qa001_numbers_format_to_e164_without_claiming_they_are_assigned(
    raw, region, e164, calling
):
    n = pn.normalize_phone(raw, region)
    assert (n.value, n.calling_code) == (e164, calling)
    assert n.masked.endswith(e164[-4:]) and e164[3:-4] not in n.masked


def test_qa002_an_unusual_but_possible_number_proceeds_marked_unusual_and_nonsense_is_refused():
    n = pn.normalize_phone("+44 7000 000000", None)
    assert n.value.startswith("+44")
    for bad in ("hello", "+1", "", "+44 12"):
        with pytest.raises(ch.ChallengeError):
            pn.normalize_phone(bad, "GB")


def test_qa010_email_keeps_the_local_part_as_typed_and_normalizes_only_the_domain():
    a = ch.normalize_email("First.Last+tag@Example.COM")
    assert a.value == "First.Last+tag@example.com"
    assert (
        ch.normalize_email("firstlast@example.com").value
        != ch.normalize_email("first.last@example.com").value
    )
    assert ch.normalize_email("user@bücher.example").value == "user@xn--bcher-kva.example"
    assert a.masked == "F***@example.com"
    for bad in ("no-at", "a@b", "a b@example.com", "<a@example.com>", "a@-bad-.com"):
        with pytest.raises(ch.ChallengeError):
            ch.normalize_email(bad)


def test_qa015_the_contact_hash_is_keyed_per_tenant():
    one = ch.contact_hash(KEY, "t-1", ch.ContactKind.EMAIL, "a@example.com")
    assert one != ch.contact_hash(KEY, "t-2", ch.ContactKind.EMAIL, "a@example.com")
    assert one != ch.contact_hash(b"x" * 32, "t-1", ch.ContactKind.EMAIL, "a@example.com")
    with pytest.raises(ch.ChallengeError):
        ch.contact_hash(b"", "t-1", ch.ContactKind.EMAIL, "a@example.com")


def test_a_phone_gets_six_digits_and_an_email_a_high_entropy_link_secret_kept_only_hashed():
    sms, code = issue()
    assert len(code) == 6 and code.isdigit() and code.encode() not in sms.secret_hash
    _mail, secret = issue(ch.Channel.EMAIL)
    assert len(secret) == 43 and ch.well_formed(ch.Channel.EMAIL, secret)
    assert sms.expires_at - NOW <= ch.TTL and sms.state is ch.State.SENT


def test_qa004_a_right_code_verifies_once():
    c, code = issue()
    first = confirm(c, code)
    assert first.verified and first.challenge.state is ch.State.VERIFIED
    again = confirm(first.challenge, code)
    assert not again.verified and again.reason == "CHALLENGE_VERIFIED"


def test_qa005_an_expired_code_fails_and_a_resend_never_extends_the_expiry():
    c, code = issue()
    late = confirm(c, code, now=NOW + timedelta(minutes=6))
    assert not late.verified and late.challenge.state is ch.State.EXPIRED
    resent, new = ch.resend(c, key=KEY, now=NOW + timedelta(seconds=61))
    assert resent.expires_at == c.expires_at and new != code and not confirm(resent, code).verified
    assert confirm(resent, new).verified


def test_resends_are_limited_in_number_and_spacing():
    c, _ = issue()
    with pytest.raises(ch.ChallengeError) as e:
        ch.resend(c, key=KEY, now=NOW + timedelta(seconds=10))
    assert e.value.code == "CHALLENGE_RATE_LIMITED"
    c2, _ = ch.resend(c, key=KEY, now=NOW + timedelta(seconds=61))
    c3, _ = ch.resend(c2, key=KEY, now=NOW + timedelta(seconds=122))
    with pytest.raises(ch.ChallengeError):
        ch.resend(c3, key=KEY, now=NOW + timedelta(seconds=183))


def test_five_wrong_codes_revoke_and_the_reason_is_the_same_for_every_wrong_code():
    c, code = issue()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        out = confirm(c, wrong)
        assert out.reason == "CHALLENGE_INVALID"
        c = out.challenge
    last = confirm(c, wrong)
    assert (
        last.challenge.state is ch.State.REVOKED
        and last.challenge.end_reason == "ATTEMPTS_EXHAUSTED"
    )
    assert not confirm(last.challenge, code).verified


def test_qa003_qa014_qa016_a_changed_contact_or_another_session_cannot_use_the_code():
    c, code = issue()
    changed = confirm(c, code, contact_revision=2)
    assert not changed.verified and changed.challenge.end_reason == "CONTACT_CHANGED"
    other = confirm(c, code, session_ref="s-2")
    assert (
        not other.verified and other.reason == "CHALLENGE_INVALID"
    )  # the same answer as a wrong code


def test_qa007_send_limits_stop_pumping_and_sms_needs_an_allowed_calling_code():
    lim = ch.parse_limits(
        {"per_contact_hour": 3, "per_case_hour": 6, "per_prefix_hour": 50, "daily_cap": {"EMAIL": 10, "SMS": 5, "VOICE": 0},
         "phone_calling_codes": ["44"]}
    )  # fmt: skip
    quiet = ch.Recent(0, 0, 0, 0)
    ch.check_send(lim, ch.Channel.SMS, quiet, calling_code="44")
    for recent, channel, cc in (
        (quiet, ch.Channel.SMS, "1"),
        (quiet, ch.Channel.VOICE, "44"),
        (ch.Recent(3, 0, 0, 0), ch.Channel.SMS, "44"),
        (ch.Recent(0, 6, 0, 0), ch.Channel.EMAIL, None),
        (ch.Recent(0, 0, 50, 0), ch.Channel.SMS, "44"),
        (ch.Recent(0, 0, 0, 5), ch.Channel.SMS, "44"),
    ):
        with pytest.raises(ch.ChallengeError):
            ch.check_send(lim, channel, recent, calling_code=cc)


def test_the_shipped_limits_send_no_sms_or_voice_anywhere():
    from zid_platform.modules.verification.application.contact_challenges import limits

    lim = limits()
    assert lim.status == "SAMPLE" and lim.phone_calling_codes == frozenset()
    assert lim.daily_cap[ch.Channel.SMS] == 0 and lim.daily_cap[ch.Channel.VOICE] == 0


def test_a_final_challenge_is_never_revoked_again():
    c, code = issue()
    done = confirm(c, code).challenge
    assert ch.revoke(done, "X") == done
    assert ch.revoke(replace(c, state=ch.State.SENT), "X").state is ch.State.REVOKED
