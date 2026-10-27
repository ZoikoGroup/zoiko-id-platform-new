"""The email template catalogue and renderer (Communication Estate ZID-EM-001..160, §02-§05).

Pre-release acceptance tests of the copybook that need no database: every template renders; the sensitive-data
rules hold; each negative case the copybook lists (token leakage, redirect injection, merge-tag injection,
header injection, unresolved placeholders) fails closed; and a golden snapshot pins the exact words of all 160
templates, so copy cannot change without a reviewed diff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from zid_platform.modules.communication.domain import catalogue as cat
from zid_platform.modules.communication.domain.catalogue import FieldKind, MessageClass, field_kind
from zid_platform.modules.communication.domain.policy import Limits, Outcome, Suppression, decide
from zid_platform.modules.communication.domain.render import (
    LinkPolicy,
    Money,
    RenderContext,
    RenderError,
    format_datetime,
    format_money,
    render,
)
from zid_platform.modules.communication.infrastructure.catalogue_file import load as load_catalogue

SNAPSHOT = Path(__file__).with_name("email_snapshots.json")
BASE = "https://id.example.com"
CATALOGUE = load_catalogue()
ROUTES = {f: f"/r/{f}" for t in CATALOGUE for f in t.url_fields}
CTX = RenderContext(
    sender_display_name="Zoiko iD",
    links=LinkPolicy(BASE, ROUTES),
    support_url=f"{BASE}/help",
    marketing_basis="you asked to receive Zoiko iD product updates",
    preferences_url=f"{BASE}/preferences",
    unsubscribe_url=f"{BASE}/email/unsubscribe?r=x&l=y&s=z",
    legal_entity="Zoiko Tech Inc.",
    mailing_address="1 Example Street",
)
WHEN = datetime(2026, 10, 25, 14, 30, tzinfo=UTC)


def sample(name: str) -> object:
    kind = field_kind(name)
    if kind is FieldKind.DATETIME:
        return WHEN
    if kind is FieldKind.MONEY:
        return Money(Decimal(2053), "USD")
    if kind is FieldKind.CODE:
        return "ABC123"
    if kind is FieldKind.URL_TOKEN:
        return "t" * 24
    if kind is FieldKind.URL_ROUTE:
        return None
    if kind is FieldKind.REFERENCE:
        return "REF-0001"
    return f"Sample {name}"


def merge_for(t: cat.Template) -> dict[str, object]:
    return {f: sample(f) for f in t.fields}


# ----------------------------------------------------------------------------------------- the catalogue


def test_the_catalogue_is_the_whole_copybook() -> None:
    assert len(CATALOGUE) == 160
    assert [t.id for t in CATALOGUE] == [f"ZID-EM-{i:03d}" for i in range(1, 161)]
    assert {k: sum(1 for t in CATALOGUE if t.message_class is k) for k in MessageClass} == {
        MessageClass.SECURITY: 76,
        MessageClass.SERVICE: 62,
        MessageClass.PRIVACY: 9,
        MessageClass.INCIDENT: 12,
        MessageClass.MARKETING: 1,
    }


def test_every_template_has_one_event_subject_preheader_and_at_most_one_action() -> None:
    for t in CATALOGUE:
        assert t.event and t.subject and t.preheader and t.paragraphs, t.id
        assert "\n" not in t.subject and "\n" not in t.preheader, t.id
        assert t.action is None or field_kind(t.action.url_field) in (
            FieldKind.URL_ROUTE,
            FieldKind.URL_TOKEN,
        ), t.id
    assert [t.id for t in CATALOGUE if t.action is None] == ["ZID-EM-009", "ZID-EM-148"]


def test_the_copybook_rules_on_sensitive_data_hold_in_the_copy() -> None:
    # §03: a one-time code only in the dedicated template; no other secret-shaped merge field anywhere
    assert [t.id for t in CATALOGUE if "one_time_code" in t.fields] == ["ZID-EM-009"]
    secretish = re.compile(
        r"password|secret|private_key|token|passkey|card_number|raw_", re.IGNORECASE
    )
    for t in CATALOGUE:
        for f in t.fields:
            if secretish.search(f):
                assert field_kind(f) is FieldKind.URL_TOKEN, (
                    t.id,
                    f,
                )  # a token only ever rides inside a link
        assert "<" not in "".join(t.paragraphs) + t.subject, (
            t.id
        )  # copy carries no markup of its own


def test_classes_follow_the_copybook_including_its_own_exceptions() -> None:
    by = {t.id: t.message_class for t in CATALOGUE}
    assert by["ZID-EM-142"] is MessageClass.MARKETING  # the one optional message
    for service in (
        "ZID-EM-143",
        "ZID-EM-144",
        "ZID-EM-145",
    ):  # confirmations of the person's own action
        assert by[service] is MessageClass.SERVICE
    assert by["ZID-EM-117"] is MessageClass.INCIDENT and by["ZID-EM-160"] is MessageClass.INCIDENT
    assert by["ZID-EM-057"] is MessageClass.PRIVACY
    assert by["ZID-EM-001"] is MessageClass.SECURITY and by["ZID-EM-130"] is MessageClass.SERVICE
    assert all(c.mandatory for c in MessageClass if c is not MessageClass.MARKETING)


def test_the_parse_kept_the_words_of_known_templates() -> None:
    t = CATALOGUE.get("ZID-EM-009")
    assert t.paragraphs[0] == "Your one-time sign-in code is: {{one_time_code}}"
    assert t.control == "Email codes are not phishing-resistant MFA; gate by policy and risk."
    t = CATALOGUE.get("ZID-EM-004")
    assert t.subject == "{{inviter_name}} invited you to {{organization_name}}"
    assert t.action is not None and t.action.label == "Review invitation"
    assert t.action.url_field == "single_use_invitation_url"
    assert CATALOGUE.get("ZID-EM-142").subject == "What’s new in Zoiko iD: {{release_public_name}}"


# ---------------------------------------------------------------------------------------- the renderer


def test_every_template_renders_with_the_wrapper_and_leaves_no_merge_tag() -> None:
    for t in CATALOGUE:
        out = render(t, merge_for(t), CTX)
        for part in (out.subject, out.preheader, out.text, out.html):
            assert not re.search(r"\{\{\s*\w+\s*\}\}", part), t.id
        assert out.text.startswith("Hello,\n"), t.id  # no name: the copybook's "Hello," fallback
        assert "Thank you," in out.text and "Zoiko iD is a Zoiko Tech Inc. platform." in out.text, (
            t.id
        )
        assert '<html lang="en">' in out.html and "<title>" in out.html and "<h1" in out.html, t.id
        if t.message_class is MessageClass.MARKETING:
            assert "Unsubscribe:" in out.text and "Manage preferences:" in out.text
            assert out.unsubscribe_url
        else:
            assert (
                "Never send us passwords, recovery codes or private keys by email." in out.text
            ), t.id
            assert out.unsubscribe_url is None and "Unsubscribe" not in out.text, t.id


def test_a_notice_without_an_action_has_no_button_and_no_url() -> None:
    for tid in ("ZID-EM-009", "ZID-EM-148"):
        out = render(CATALOGUE.get(tid), merge_for(CATALOGUE.get(tid)), CTX)
        assert 'class="btn"' not in out.html and "https://" not in out.text.split("--")[0], tid
    with_action = render(CATALOGUE.get("ZID-EM-001"), merge_for(CATALOGUE.get("ZID-EM-001")), CTX)
    assert 'class="btn"' in with_action.html
    assert (
        f"{BASE}/r/single_use_verification_url?t={'t' * 24}" in with_action.text
    )  # the destination is spelled out


def test_the_greeting_uses_a_permitted_name_and_falls_back_safely() -> None:
    t = CATALOGUE.get("ZID-EM-003")
    assert render(
        t, {**merge_for(t), "recipient_display_name": "Ada Lovelace"}, CTX
    ).text.startswith("Hello Ada Lovelace,\n")
    assert render(t, {**merge_for(t), "recipient_display_name": "  "}, CTX).text.startswith(
        "Hello,\n"
    )
    assert render(t, {**merge_for(t), "recipient_display_name": "{{evil}}"}, CTX).text.startswith(
        "Hello,\n"
    )


def test_unresolved_unknown_oversized_and_malformed_values_fail_closed() -> None:
    t = CATALOGUE.get("ZID-EM-004")
    good = merge_for(t)
    missing = {k: v for k, v in good.items() if k != "organization_name"}
    for merge, code in (
        (missing, "MERGE_MISSING"),
        ({**good, "surprise": "x"}, "MERGE_UNKNOWN"),
        ({**good, "inviter_name": "x" * 201}, "MERGE_TOO_LONG"),
        (
            {**good, "inviter_name": "{{organization_name}}"},
            "MERGE_INVALID",
        ),  # no merge-tag injection
        ({**good, "inviter_name": ""}, "MERGE_MISSING"),
        ({**good, "expires_at_local": "tomorrow"}, "MERGE_INVALID"),
        (
            {**good, "expires_at_local": datetime(2026, 1, 1)},
            "MERGE_INVALID",
        ),  # a naive time is ambiguous
    ):
        with pytest.raises(RenderError) as e:
            render(t, merge, CTX)
        assert e.value.code == code, (merge, e.value.code)
    assert (
        str(RenderError("MERGE_MISSING", "organization_name")) == "MERGE_MISSING:organization_name"
    )


def test_user_content_is_escaped_and_cannot_break_out_of_the_html_or_a_header() -> None:
    t = CATALOGUE.get("ZID-EM-007")  # a person's name appears in subject and body
    attack = {
        **merge_for(t),
        "member_display_name": '<img src=x onerror=alert(1)> "quoted"\r\nBcc: evil@example.com',
    }
    out = render(t, attack, CTX)
    assert "<img" not in out.html and "&lt;img" in out.html
    assert "\r" not in out.subject and "\n" not in out.subject  # no header injection
    assert "Bcc:" not in out.subject.split("joined")[0] or "\n" not in out.subject


def test_a_security_message_header_never_carries_a_link_or_an_address() -> None:
    t = CATALOGUE.get("ZID-EM-004")  # SECURITY: the inviter's name is in the subject
    for hostile in ("click https://evil.example", "ceo@evil.example", "www.evil.example"):
        with pytest.raises(RenderError) as e:
            render(t, {**merge_for(t), "inviter_name": hostile}, CTX)
        assert e.value.code == "HEADER_UNSAFE"
    assert render(
        CATALOGUE.get("ZID-EM-004"), {**merge_for(t), "inviter_name": "Ada L."}, CTX
    ).subject.startswith("Ada L.")


def test_links_come_only_from_the_allowlist_and_a_token_is_opaque() -> None:
    t = CATALOGUE.get("ZID-EM-001")
    good = merge_for(t)
    # a caller-supplied URL is never used: only an opaque token is accepted for a single-use link
    for bad in ("https://evil.example/x", "javascript:alert(1)", "a b", "short", "t" * 600, 5):
        with pytest.raises(RenderError) as e:
            render(t, {**good, "single_use_verification_url": bad}, CTX)
        assert e.value.code == "MERGE_INVALID"
    # a route field takes at most a plain object id appended to its allowlisted path
    t2 = CATALOGUE.get("ZID-EM-130")
    for bad in ("https://evil.example", "../admin", "a/b", "x" * 80):
        with pytest.raises(RenderError):
            render(t2, {**merge_for(t2), "trusted_billing_url": bad}, CTX)
    ok = render(t2, {**merge_for(t2), "trusted_billing_url": "inv_123"}, CTX)
    assert f"{BASE}/r/trusted_billing_url/inv_123" in ok.text
    # no route configured means no link and no message
    no_route = RenderContext("Zoiko iD", LinkPolicy(BASE, {}), f"{BASE}/help")
    with pytest.raises(RenderError) as e:
        render(t2, merge_for(t2), no_route)
    assert e.value.code == "ROUTE_UNCONFIGURED"


def test_the_link_origin_must_be_bare_https_and_routes_plain_paths() -> None:
    for base in (
        "http://id.example.com",
        "https://id.example.com/",
        "https://id.example.com?x=1",
        "https://u@id.example.com",
        "ftp://x",
    ):
        with pytest.raises(ValueError):
            LinkPolicy(base, {})
    LinkPolicy("http://localhost:3000", {}, allow_localhost_http=True)
    with pytest.raises(ValueError):
        LinkPolicy("http://localhost:3000", {})  # plain http only where explicitly allowed
    for path in ("no-slash", "//evil", "/a/../b", "/a?x=1", "/a#f"):
        with pytest.raises(ValueError):
            LinkPolicy(BASE, {"f": path})


def test_the_one_time_code_has_a_strict_shape() -> None:
    t = CATALOGUE.get("ZID-EM-009")
    out = render(t, merge_for(t), CTX)
    assert "Your one-time sign-in code is: ABC123" in out.text
    for bad in ("12 34", "<b>1</b>", "abc123", "1", "1" * 11):
        with pytest.raises(RenderError):
            render(t, {**merge_for(t), "one_time_code": bad}, CTX)


def test_a_marketing_message_needs_its_whole_footer() -> None:
    t = CATALOGUE.get("ZID-EM-142")
    bare = RenderContext("Zoiko iD", LinkPolicy(BASE, ROUTES), f"{BASE}/help")
    with pytest.raises(RenderError) as e:
        render(t, merge_for(t), bare)
    assert e.value.code == "MARKETING_FOOTER_INCOMPLETE"


def test_dates_and_money_follow_the_recipient_and_never_guess() -> None:
    assert format_datetime(WHEN, "UTC") == "Oct 25, 2026, 2:30 PM UTC"
    assert (
        format_datetime(datetime(2026, 1, 2, 0, 5, tzinfo=UTC), "UTC")
        == "Jan 2, 2026, 12:05 AM UTC"
    )
    assert (
        format_datetime(WHEN, "Not/AZone") == "Oct 25, 2026, 2:30 PM UTC"
    )  # a bad preference never fails a send
    assert format_money(Money(Decimal(2053), "USD")) == "USD 2,053.00"
    assert format_money(Money(Decimal(1500), "JPY")) == "JPY 1,500"
    with pytest.raises(RenderError):
        format_money(Money(Decimal(1), "usd!"))


def test_every_rendered_template_matches_its_golden_snapshot() -> None:
    """Subject, preheader, text and HTML of all 160 templates, pinned. A copy or wrapper change shows up here as a
    diff to review; regenerate deliberately with ZID_UPDATE_SNAPSHOTS=1 and commit the new file."""
    current: dict[str, str] = {}
    for t in CATALOGUE:
        out = render(t, merge_for(t), CTX)
        blob = f"{t.version}\x1f{out.subject}\x1f{out.preheader}\x1f{out.text}\x1f{out.html}"
        current[t.id] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    if os.environ.get("ZID_UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    pinned = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changed = sorted(k for k in current if pinned.get(k) != current[k])
    assert not changed, (
        f"rendered copy changed for {changed[:5]} (and {max(0, len(changed) - 5)} more)"
    )
    assert set(pinned) == set(current)


# ---------------------------------------------------------------------------------------------- policy


def test_policy_blocks_marketing_only_by_opt_out_and_everything_by_a_hard_bounce() -> None:
    def verdict(
        cls: MessageClass,
        sup: set[Suppression],
        consent: str | None,
        t: int = 0,
        r: int = 0,
        **kw: int,
    ):
        return decide(
            message_class=cls,
            suppressions=sup,
            consent=consent,
            template_sent_last_hour=t,
            recipient_sent_last_hour=r,
            limits=Limits(**kw) if kw else None,
        )

    sec, mkt = MessageClass.SECURITY, MessageClass.MARKETING
    assert verdict(sec, set(), None).send
    assert verdict(
        sec, {Suppression.UNSUBSCRIBED, Suppression.COMPLAINT}, "WITHDRAWN"
    ).send  # §03: never suppressed
    assert verdict(sec, {Suppression.BOUNCE_HARD}, None).reason == "BOUNCE_HARD"
    assert verdict(mkt, {Suppression.BOUNCE_HARD}, "GRANTED").reason == "BOUNCE_HARD"
    assert verdict(mkt, set(), None).reason == "NO_CONSENT"
    assert verdict(mkt, set(), "WITHDRAWN").reason == "UNSUBSCRIBED"
    assert verdict(mkt, {Suppression.UNSUBSCRIBED}, "GRANTED").reason == "UNSUBSCRIBED"
    assert verdict(mkt, {Suppression.COMPLAINT}, "GRANTED").reason == "COMPLAINT"
    assert verdict(mkt, set(), "GRANTED").send


def test_policy_throttles_a_flood_per_template_and_per_recipient() -> None:
    kw = {"message_class": MessageClass.SERVICE, "suppressions": set(), "consent": None}
    limits = Limits(per_template_per_hour=2, per_recipient_per_hour=5)
    d = decide(**kw, template_sent_last_hour=2, recipient_sent_last_hour=2, limits=limits)  # type: ignore[arg-type]
    assert d.outcome is Outcome.THROTTLED and d.reason == "TEMPLATE_LIMIT"
    d = decide(**kw, template_sent_last_hour=1, recipient_sent_last_hour=5, limits=limits)  # type: ignore[arg-type]
    assert d.reason == "RECIPIENT_LIMIT"
    assert decide(**kw, template_sent_last_hour=1, recipient_sent_last_hour=4, limits=limits).send  # type: ignore[arg-type]
