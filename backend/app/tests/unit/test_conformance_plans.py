"""The conformance plans claim only what the server really supports (tests/conformance/plans.yaml).

A plan whose variant the server does not advertise would test nothing useful (or fail for the
wrong reason), and a profile with no OIDF suite must still name the in-repo tests covering it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import yaml
from zid_platform.modules.credential.application.verifier import RESPONSE_MODES
from zid_platform.modules.session.application.oauth_server import metadata
from zid_platform.modules.signals.application.ssf import ssf_configuration
from zid_platform.settings import Settings

REPO = Path(__file__).resolve().parents[4]
DOC: dict[str, Any] = yaml.safe_load((REPO / "tests/conformance/plans.yaml").read_text("utf-8"))
META = metadata("https://id.example", oidc=True)


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_conformance", REPO / "tests/conformance/run_conformance.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plans_are_complete_and_secret_free() -> None:
    assert DOC["suite"]["release"]
    for name, spec in DOC["plans"].items():
        assert {"requirement", "plan", "variant", "config"} <= set(spec), name
        assert "alias" in spec["config"] or spec["plan"].startswith("oidcc-config"), name
        text = yaml.safe_dump(spec)
        assert not re.search(r"-----BEGIN|\"d\":", text), f"{name}: key material inline"


def test_oauth_variants_match_server_metadata() -> None:
    auth = set(META["token_endpoint_auth_methods_supported"])
    for name, spec in DOC["plans"].items():
        v = spec["variant"]
        if v.get("client_auth_type") == "private_key_jwt":
            assert "private_key_jwt" in auth, name
        if v.get("client_auth_type") == "mtls":
            assert "tls_client_auth" in auth, name
        if v.get("sender_constrain") == "dpop":
            assert META["dpop_signing_alg_values_supported"], name
        if v.get("sender_constrain") == "mtls":
            assert META["tls_client_certificate_bound_access_tokens"] is True, name
        if "response_type" in v:
            assert v["response_type"] in META["response_types_supported"], name
        if v.get("openid") == "openid_connect" or spec["plan"].startswith("oidcc"):
            assert "userinfo_endpoint" in META, name


def test_verifier_and_ssf_variants_match_what_we_build() -> None:
    prefixes = Settings.model_fields["verifier_client_id_prefix"].annotation.__args__  # type: ignore[union-attr]
    ssf = ssf_configuration("https://id.example")
    for name, spec in DOC["plans"].items():
        v = spec["variant"]
        if spec["plan"].startswith("oid4vp"):
            assert v["client_id_prefix"] in prefixes, name
            assert v["response_mode"] in RESPONSE_MODES, name
        if spec["plan"].startswith("openid-ssf"):
            push = "urn:ietf:rfc:8935"
            assert v["ssf_delivery_mode"] != "push" or push in ssf["delivery_methods_supported"]


def test_uncovered_profiles_name_existing_tests() -> None:
    for profile, where in DOC["not_covered_by_oidf_suite"].items():
        for test_file in re.findall(r"test_\w+\.py", where):
            found = list((REPO / "backend/app/tests").rglob(test_file))
            assert found, f"{profile}: {test_file} does not exist"


def test_runner_lists_and_refuses_safely(capsys: Any, monkeypatch: Any) -> None:
    runner = _runner()
    assert runner.main(["--list"]) == 0
    assert "oidc-op-basic" in capsys.readouterr().out
    assert runner.main(["no-such-plan"]) == 2
    for var in list(runner.placeholders(DOC)):
        monkeypatch.delenv(var, raising=False)
    assert runner.main(["oidc-op-basic"]) == 2  # missing environment, nothing sent
    assert "missing environment" in capsys.readouterr().out
    filled = runner.fill({"a": "${X}", "j": "${J}"}, {"X": "1", "J": '{"keys": []}'})
    assert filled == {"a": "1", "j": {"keys": []}}
