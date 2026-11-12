"""Public / private isolation (G0; ADR-01, ADR-02, INV-01, OD-001..003). The assistant must never be able to reach identity systems.

These are static and structural proofs that run in CI. The runtime deny proofs are in test_p0_scenarios.py (P0-02); the network-level
deny matrix for a deployed environment is checked by the SRE evidence pack (docs/phases/phase-m-public-assistant.md, G0).
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
from zid_assistant.settings import AssistantSettings

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "zid_assistant"
PLATFORM = ROOT.parent / "app"
BACKEND = ROOT.parent


def py_files(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


def imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_the_assistant_never_imports_the_platform() -> None:
    bad = [
        str(p.relative_to(ROOT))
        for p in py_files(SRC)
        if any(m == "zid_platform" or m.startswith("zid_platform.") for m in imports(p))
    ]
    assert not bad, f"the assistant must not import the platform: {bad}"


def test_the_assistant_depends_on_no_identity_library() -> None:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    names = {re.split(r"[<>=\[ ;]", d, maxsplit=1)[0] for d in deps}
    forbidden = {
        "zoiko-id-platform",
        "zid-authz-client",
        "zid-crypto",
        "zid-evidence",
        "zid-outbox",
        "webauthn",
        "signxml",
        "cedarpy",
        "google-cloud-kms",
        "scim2-models",
        "zid-idempotency",
    }
    assert not (names & forbidden), names & forbidden
    allowed_zid = {n for n in names if n.startswith("zid-")}
    assert allowed_zid <= {"zid-app", "zid-errors", "zid-telemetry"}, allowed_zid


def test_the_platform_does_not_depend_on_or_import_the_assistant() -> None:
    deps = tomllib.loads((PLATFORM / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    assert not any("assistant" in d for d in deps)
    bad = [
        str(p)
        for p in py_files(PLATFORM / "src")
        if any(m == "zid_assistant" or m.startswith("zid_assistant.") for m in imports(p))
    ]
    assert not bad, bad


def test_no_shared_library_imports_either_side() -> None:
    """The shared libs are mechanics only: they know nothing about the assistant, and the assistant uses only the three shell libs."""
    used = {m.split(".")[0] for p in py_files(SRC) for m in imports(p) if m.startswith("zid_")}
    assert used - {"zid_assistant", "zid_app", "zid_errors", "zid_telemetry"} == set(), used


IDENTITY_WORDS = (
    "issuer",
    "signing",
    "tenant",
    "webauthn",
    "oauth",
    "spicedb",
    "kafka",
    "jwks",
    "principal",
    "authority",
    "mandate",
    "credential",
    "passkey",
    "keystore",
    "kms",
)


def test_settings_hold_no_identity_tenant_or_signing_configuration() -> None:
    names = list(AssistantSettings.model_fields)
    bad = [n for n in names if any(w in n for w in IDENTITY_WORDS)]
    assert not bad, f"identity-shaped settings do not belong here: {bad}"
    assert AssistantSettings.model_config["env_prefix"] == "ZID_ASSISTANT_", (
        "own prefix: it never reads the platform's ZID_* settings"
    )


def test_every_flag_defaults_off() -> None:
    s = AssistantSettings(_env_file=None)
    assert not (
        s.enabled
        or s.ai_answers
        or s.intake_sales
        or s.intake_support
        or s.marketing
        or s.kill_switch
    )
    assert s.model_provider == "mock" and s.intake_countries == [] and s.intake_destinations == {}


def test_only_the_model_gateway_and_intake_make_outbound_requests() -> None:
    """Outbound HTTP exists only for the model gateway and the intake receivers; nothing else may call out."""
    outbound = {
        "httpx",
        "anthropic",
        "requests",
        "aiohttp",
        "urllib3",
        "urllib.request",
        "socket",
        "smtplib",
        "grpc",
    }
    users = {
        str(p.relative_to(SRC)).replace("\\", "/") for p in py_files(SRC) if imports(p) & outbound
    }
    assert users <= {
        "infrastructure/delivery.py",
        "infrastructure/model/claude.py",
        "infrastructure/model/http_providers.py",
    }, users


def test_no_source_names_an_identity_route_or_service() -> None:
    pattern = re.compile(
        r"/oauth2|/authn|/v1/tenant|/v1/principals|/scim|webauthn|spicedb|/token\b|openid-configuration",
        re.IGNORECASE,
    )
    hits = []
    for p in py_files(SRC):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if (
                pattern.search(line)
                and not line.lstrip().startswith(("#", '"""'))
                and "FORBIDDEN" not in line
                and "forbidden" not in line.lower()
            ):
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert not hits, hits


def test_the_database_role_can_touch_only_its_own_schema() -> None:
    sql = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "sql").glob("*.sql"))
    schemas = set(re.findall(r"\b([a-z_]+)\.(?:[a-z_]+)\b(?=\s*[(\s,;])", sql))
    platform_schemas = {
        "core",
        "org",
        "authn",
        "verify",
        "cred",
        "authz",
        "agent",
        "privacy",
        "evidence",
        "signals",
        "events",
        "metering",
        "commercial",
        "keys",
        "zid_migrations",
        "platform",
        "global_directory",
    }
    assert not (schemas & platform_schemas), schemas & platform_schemas
    mig = (SRC / "migrate.py").read_text(encoding="utf-8")
    assert "GRANT" in mig and "public_assistant" in mig
    assert not re.search(
        r"GRANT[^;]*\bON\s+(?:ALL TABLES IN SCHEMA|SCHEMA)\s+(?!public_assistant)\w+", mig
    ), "grants outside the assistant schema"
    assert "NOSUPERUSER" in mig and "NOBYPASSRLS" in mig


def test_visitor_text_is_never_passed_to_a_store_or_a_logger() -> None:
    """Structural guard for RET-01/PR-02: the store interface has no parameter that could carry visitor chat text."""
    from zid_assistant.infrastructure.store import Store

    forbidden = {"text", "message", "prompt", "content", "transcript", "completion", "body"}
    for name in dir(Store):
        if name.startswith("_"):
            continue
        params = {
            p
            for p in getattr(Store, name).__code__.co_varnames[
                : getattr(Store, name).__code__.co_argcount
            ]
        }
        assert not (params & forbidden), (name, params & forbidden)


@pytest.mark.parametrize(
    "name", ["feedback_event", "message_metric", "safety_event", "audit_event"]
)
def test_analytics_tables_have_no_free_text_column(name: str) -> None:
    sql = (ROOT / "sql" / "001_init.sql").read_text(encoding="utf-8")
    block = re.search(
        rf"CREATE TABLE IF NOT EXISTS public_assistant\.{name} \((.*?)\n\);", sql, re.DOTALL
    )
    assert block, name
    cols = re.findall(r"^\s+([a-z_]+)\s+(\w+)", block.group(1), re.MULTILINE)
    for col, typ in cols:
        assert not any(
            w in col
            for w in (
                "text",
                "message_body",
                "prompt",
                "answer",
                "transcript",
                "ip",
                "email",
                "session",
            )
        ) or col in ("message_metric",), (name, col)
        assert typ.lower() in {"uuid", "text", "boolean", "integer", "timestamptz"}, (
            name,
            col,
            typ,
        )
