"""The sender-domain checker (Communication Estate: SPF, DKIM, DMARC alignment before production mail). Offline: DNS is a fake resolver."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
TOOL = REPO / "tools" / "scripts" / "check_sender_domain.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zid_tool_sender_domain", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


T = _load()


def _levels(findings: list) -> dict[str, str]:
    return {f.record: f.level for f in findings}


def test_a_complete_strict_setup_has_no_error_or_warning() -> None:
    dns = {
        "mail.example": ["v=spf1 include:_spf.provider.example -all"],
        "_dmarc.mail.example": [
            "v=DMARC1; p=reject; rua=mailto:dmarc@mail.example; adkim=s; aspf=s"
        ],
        "s1._domainkey.mail.example": ["v=DKIM1; k=rsa; p=MIIBIjANBgkq"],
    }
    out = T.check_domain("Mail.Example.", ["s1"], lambda n: dns.get(n, []))
    assert set(_levels(out).values()) == {"OK"}


@pytest.mark.parametrize(
    ("spf", "level"),
    [
        ([], "ERROR"),
        (["v=spf1 +all"], "ERROR"),
        (["v=spf1 ?all"], "ERROR"),
        (["v=spf1 include:a.example"], "ERROR"),
        (["v=spf1 -all", "v=spf1 -all"], "ERROR"),
        (["v=spf1 include:a.example ~all"], "WARN"),
        (["v=spf1 include:a.example -all"], "OK"),
    ],
)
def test_spf_policy_is_graded(spf: list[str], level: str) -> None:
    assert T.check_spf(spf)[0].level == level


def test_spf_over_ten_lookups_is_an_error() -> None:
    record = "v=spf1 " + " ".join(f"include:s{i}.example" for i in range(11)) + " -all"
    assert any("limit is 10" in f.message for f in T.check_spf([record]))


@pytest.mark.parametrize(
    ("record", "level"),
    [
        ([], "ERROR"),
        (["v=DMARC1; p=none; rua=mailto:a@b.example"], "WARN"),
        (["v=DMARC1; p=bogus; rua=mailto:a@b.example"], "ERROR"),
        (["v=DMARC1; p=reject"], "WARN"),
        (["v=DMARC1; p=quarantine; rua=mailto:a@b.example; pct=50"], "WARN"),
        (["v=DMARC1; p=reject; rua=mailto:a@b.example"], "OK"),
    ],
)
def test_dmarc_policy_is_graded(record: list[str], level: str) -> None:
    assert T.check_dmarc(record)[0].level == level


def test_dkim_missing_revoked_and_test_keys_are_not_passes() -> None:
    assert T.check_dkim("s", [])[0].level == "ERROR"
    assert T.check_dkim("s", ["v=DKIM1; p="])[0].level == "ERROR"
    assert T.check_dkim("s", ["v=DKIM1; t=y; p=AAAA"])[0].level == "WARN"
    assert T.check_dkim("s", ["v=DKIM1; p=AAAA"])[0].level == "OK"


def test_no_selector_given_says_dkim_was_not_checked() -> None:
    out = T.check_domain("mail.example", [], lambda n: [])
    assert _levels(out)["DKIM"] == "WARN"


def test_a_dns_failure_is_exit_2_never_a_pass_or_a_missing_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(name: str) -> list[str]:
        raise T.LookupFailed("resolver down")

    assert T.main(["mail.example"], resolve=boom) == 2
    assert "could not read DNS" in capsys.readouterr().err


def test_exit_code_is_one_on_any_error_and_zero_otherwise() -> None:
    bad = lambda n: []
    assert T.main(["mail.example", "--dkim-selector", "s1"], resolve=bad) == 1
    good = {
        "mail.example": ["v=spf1 -all"],
        "_dmarc.mail.example": ["v=DMARC1; p=reject; rua=mailto:a@b.example"],
        "s1._domainkey.mail.example": ["v=DKIM1; p=AAAA"],
    }
    assert T.main(["mail.example", "--dkim-selector", "s1"], resolve=lambda n: good.get(n, [])) == 0
