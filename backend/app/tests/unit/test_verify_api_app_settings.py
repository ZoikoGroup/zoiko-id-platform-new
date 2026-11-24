"""The rules for an application's workflow entitlement and hosted-page settings (CR-156)."""

from __future__ import annotations

import pytest
from zid_platform.modules.verify_api.domain import app_settings as a
from zid_platform.modules.verify_api.domain import model as m


def refused(fn, *args, **kw) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(m.PartnerError) as e:
        fn(*args, **kw)
    return e.value.code


def test_branding_keeps_only_the_four_approved_settings() -> None:
    clean = a.check_branding(
        {
            "display_name": "  Acme   Bank ",
            "theme": "green",
            "support_url": "https://acme.example/help",
            "locale": "en-GB",
        }
    )
    assert clean == {
        "display_name": "Acme Bank",
        "theme": "green",
        "support_url": "https://acme.example/help",
        "locale": "en-GB",
    }
    assert a.check_branding(None) == {} and a.check_branding({}) == {}
    assert (
        a.check_branding({"display_name": "", "theme": "", "support_url": None}) == {}
    )  # blank means not set


def test_branding_refuses_markup_unknown_themes_and_unsafe_links() -> None:
    bad: list[dict[str, object]] = [
        {"display_name": "A"},
        {"display_name": "x" * 61},
        {"display_name": "<b>Acme</b>"},
        {"display_name": "Acme\u0007"},
        {"display_name": 5},
        {"theme": "red"},
        {"theme": "#ff0000"},
        {"support_url": "http://acme.example"},
        {"support_url": "javascript:alert(1)"},
        {"support_url": "https://user:pw@acme.example"},
        {"support_url": "https://acme.example/#x"},
        {"support_url": "https://" + "a" * 200 + ".example"},
        {"locale": "english"},
        {"locale": "en_GB"},
        {"css": "body{}"},
        {"logo": "https://acme.example/l.png"},
    ]
    for b in bad:
        assert refused(a.check_branding, b) == "VALIDATION_FAILED", b
    assert refused(a.check_branding, ["x"]) == "VALIDATION_FAILED"  # type: ignore[arg-type]


def test_workflows_must_be_approved_ones_and_the_default_must_be_allowed() -> None:
    assert a.check_workflows(None, None) == ([], None)
    assert a.check_workflows(["wf_document_only", "wf_document_only"], "wf_document_only") == (
        ["wf_document_only"],
        "wf_document_only",
    )
    assert a.check_workflows([], "wf_document_selfie") == (
        [],
        "wf_document_selfie",
    )  # empty = all, so any approved default
    for allowed, default in (
        (["wf_nope"], None),
        ([5], None),
        (["wf_document_only"], "wf_document_selfie"),
        ([], "wf_nope"),
        ("wf_document_only", None),
    ):
        assert refused(a.check_workflows, allowed, default) == "VALIDATION_FAILED", (
            allowed,
            default,
        )


def test_entitlement_and_the_default_workflow() -> None:
    assert a.entitled([], "wf_document_selfie") and a.entitled([], "wf_document_only")
    assert a.entitled(["wf_document_only"], "wf_document_only") and not a.entitled(
        ["wf_document_only"], "wf_document_selfie"
    )
    assert a.default_workflow([], None) == "wf_document_selfie"
    assert (
        a.default_workflow(["wf_document_only"], None) == "wf_document_only"
    )  # the platform default is not allowed: the first allowed
    assert (
        a.default_workflow(["wf_document_only", "wf_document_selfie"], "wf_document_only")
        == "wf_document_only"
    )


def test_the_themes_are_a_closed_approved_set() -> None:
    assert set(a.THEMES) == {"blue", "green", "violet", "slate"} and a.DEFAULT_THEME in a.THEMES
