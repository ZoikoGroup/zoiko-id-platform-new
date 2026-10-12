"""Architecture baseline guards (Phase A; ZID-ARCH-002 §4, §5.1, §22-23).

Two jobs:
1. Keep the baseline registries in contracts/baseline/ honest: every reference resolves, every
   module has one capability, statuses match their evidence.
2. Enforce the invariants that can already be checked statically (INV-05, INV-11, INV-13) and
   the pure-domain rule (DEC-A2).
"""

import ast
import csv
import re
import sys
from pathlib import Path

import yaml

APP = Path(__file__).resolve().parents[2]
BACKEND = APP.parent
REPO = BACKEND.parent
MODULES_DIR = APP / "src" / "zid_platform" / "modules"
BASELINE = REPO / "contracts" / "baseline"
PHASES = REPO / "docs" / "phases"

INVARIANTS = yaml.safe_load((BASELINE / "invariants.yaml").read_text(encoding="utf-8"))[
    "invariants"
]
CAPABILITIES = yaml.safe_load((BASELINE / "capabilities.yaml").read_text(encoding="utf-8"))[
    "capabilities"
]
GATES = yaml.safe_load((BASELINE / "gates.yaml").read_text(encoding="utf-8"))
OWNERSHIP = yaml.safe_load((BACKEND / "database" / "table-ownership.yaml").read_text("utf-8"))[
    "tables"
]
ON_DISK = {p.name for p in MODULES_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")}
PHASE_LETTERS = set("ABCDEFGHI")


def _decisions() -> dict[str, str]:
    """DEC id -> status, read from the phase files."""
    found: dict[str, str] = {}
    for md in PHASES.glob("phase-*.md"):
        text = md.read_text(encoding="utf-8")
        for m in re.finditer(
            r"^### (DEC-[A-I]\d+):.*?^- \*\*Status:\*\* (\w+)", text, re.MULTILINE | re.DOTALL
        ):
            found[m.group(1)] = m.group(2)
    return found


DECISIONS = _decisions()
ACCEPTANCE_TESTS = {
    row["test_id"]
    for row in csv.DictReader(
        (REPO / "docs" / "traceability" / "acceptance-tests.csv").open(encoding="utf-8")
    )
}
SPEC_IDS = set(
    re.findall(r"\| (ZID-[A-Z-]+\d{3})", (REPO / "docs/specs/README.md").read_text("utf-8"))
)


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            names.append(node.module or "")
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


# ---------------------------------------------------------------- registries


def test_decision_ids_are_parsed_from_phase_files() -> None:
    assert len(DECISIONS) >= 30, DECISIONS


def test_invariants_cover_section_4() -> None:
    assert sorted(INVARIANTS) == [f"INV-{i:02d}" for i in range(1, 16)]


def test_invariant_references_resolve() -> None:
    problems = []
    for inv, spec in INVARIANTS.items():
        if spec["phase"] not in PHASE_LETTERS:
            problems.append(f"{inv}: unknown phase {spec['phase']}")
        for e in spec["enforcement"]:
            kind, ref = e["kind"], e["ref"]
            if kind == "architecture-test":
                file, func = ref.split("::")
                path = BACKEND / file
                if not path.exists() or func not in _functions(path):
                    problems.append(f"{inv}: missing test {ref}")
            elif kind == "acceptance-test" and ref not in ACCEPTANCE_TESTS:
                problems.append(f"{inv}: unknown acceptance test {ref}")
            elif kind == "decision" and ref not in DECISIONS:
                problems.append(f"{inv}: unknown decision {ref}")
            elif kind not in {"architecture-test", "acceptance-test", "decision"}:
                problems.append(f"{inv}: unknown enforcement kind {kind}")
    assert not problems, "\n".join(problems)


def test_invariant_status_matches_enforcement() -> None:
    problems = []
    for inv, spec in INVARIANTS.items():
        kinds = {e["kind"] for e in spec["enforcement"]}
        has_test = "architecture-test" in kinds
        later_proof = bool(kinds & {"acceptance-test", "decision"})
        ok = {
            "enforced": has_test,
            "partial": has_test and later_proof,
            "planned": not has_test and later_proof,
        }.get(spec["status"], False)
        if not ok:
            problems.append(f"{inv}: status {spec['status']!r} does not match {sorted(kinds)}")
    assert not problems, "\n".join(problems)


def test_every_module_has_exactly_one_capability() -> None:
    listed = [m for c in CAPABILITIES.values() for m in c["modules"]]
    duplicates = {m for m in listed if listed.count(m) > 1}
    assert not duplicates, f"modules in two capabilities: {duplicates}"
    assert set(listed) == ON_DISK, f"capabilities and modules/ differ: {set(listed) ^ ON_DISK}"
    for name, cap in CAPABILITIES.items():
        assert cap["modules"] or cap.get("hosted_by") in CAPABILITIES, f"{name} has no home"


def test_capabilities_never_own_what_they_must_not() -> None:
    module_cap = {m: name for name, c in CAPABILITIES.items() for m in c["modules"]}
    violations = []
    for table, spec in OWNERSHIP.items():
        cap_name = module_cap.get(spec["module"])
        if cap_name is None:  # platform streams such as outbox
            continue
        caps = [CAPABILITIES[cap_name]] + [
            c for c in CAPABILITIES.values() if c.get("hosted_by") == cap_name
        ]
        for cap in caps:
            if table.split(".")[0] in cap.get("forbidden_schemas", []):
                violations.append(
                    f"{table} ({spec['module']}) is in a schema {cap['name']} must not own"
                )
            if table in cap.get("forbidden_tables", []):
                violations.append(
                    f"{table} ({spec['module']}) is a table {cap['name']} must not own"
                )
    assert not violations, "\n".join(violations)


def test_gate_register_is_complete_and_resolves() -> None:
    assert sorted(GATES["p0"]) == [f"P0-{i:02d}" for i in range(1, 11)]
    assert sorted(GATES["p1"]) == [f"P1-{i:02d}" for i in range(1, 12)]
    assert sorted(GATES["baseline_gates"]) == [f"BG-{i:02d}" for i in range(1, 12)]
    assert sorted(GATES["baseline_decisions"]) == [f"ADR-{i:02d}" for i in range(1, 11)]
    problems = [
        f"{k}: unknown {v}" for k, v in GATES["baseline_decisions"].items() if v not in DECISIONS
    ]
    for group in ("p0", "p1", "baseline_gates"):
        for gid, g in GATES[group].items():
            if g["phase"] not in PHASE_LETTERS:
                problems.append(f"{gid}: unknown phase")
            problems += [
                f"{gid}: unknown decision {d}" for d in g.get("closed_by", []) if d not in DECISIONS
            ]
            problems += [
                f"{gid}: unknown test {t}"
                for t in g.get("acceptance", [])
                if t not in ACCEPTANCE_TESTS
            ]
            if (
                group != "baseline_gates"
                and not g["closed_by"]
                and g.get("closed_by_spec") not in SPEC_IDS
            ):
                problems.append(f"{gid}: needs a closing decision or a registered closing spec")
            if g["status"] not in {"open", "in-progress", "closed"}:
                problems.append(f"{gid}: bad status {g['status']}")
            if g["status"] == "closed" and not g.get("evidence"):
                problems.append(
                    f"{gid}: closed without evidence (narrative assurance is not evidence)"
                )
    assert not problems, "\n".join(problems)


def test_baseline_ratification_gate_matches_decision_status() -> None:
    ratified = all(DECISIONS[d] == "Accepted" for d in GATES["baseline_decisions"].values())
    if GATES["baseline_gates"]["BG-01"]["status"] == "closed":
        assert ratified, "BG-01 is closed but not every baseline decision is Accepted"


# ---------------------------------------------------------------- invariants

AI_MODEL_SDKS = {
    "openai",
    "anthropic",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "llama_index",
    "transformers",
    "google.generativeai",
    "google.genai",
    "cohere",
    "mistralai",
    "litellm",
    "ollama",
}
AUTHORITY_MODULES = (
    "authority",
    "mandate",
    "decision",
    "policy_runtime",
    "policy_authoring",
    "relationship",
    "organization",
)


def test_no_ai_model_sdk_in_authority_modules() -> None:
    """INV-05: AI never supplies authority, so no model SDK sits on the authority path."""
    violations = [
        f"{py.relative_to(APP)} imports {name}"
        for module in AUTHORITY_MODULES
        for py in (MODULES_DIR / module).rglob("*.py")
        for name in _imports(py)
        if name in AI_MODEL_SDKS or name.split(".")[0] in AI_MODEL_SDKS
    ]
    assert not violations, "\n".join(violations)


PUBLIC_CONTRACT_DIRS = (
    "openapi/public",
    "openapi/developer",
    "openapi/tenant-admin",
    "openapi/authzen",
    "openapi/operator",
    "asyncapi",
)
DRAFT_MARKERS = (
    "x-zid-draft",  # explicit marker for anything not yet standardized
    "txn_token",
    "transaction-token",
    "transaction_token",  # IETF Transaction Tokens (draft)
    "identity-chaining",
    "id-jag",  # IETF Identity Chaining / ID-JAG (draft)
    "tce_version",
    "transaction_context_envelope",  # internal TCE, never public (DEC-D3)
)


def test_public_contracts_expose_no_draft_protocol() -> None:
    """INV-11 / BG-11: drafts stay behind adapters, never in a frozen public contract."""
    violations = []
    for d in PUBLIC_CONTRACT_DIRS:
        for f in (REPO / "contracts" / d).rglob("*"):
            if f.is_file() and f.suffix in {".yaml", ".yml", ".json"}:
                text = f.read_text(encoding="utf-8").lower()
                violations += [f"{f.relative_to(REPO)}: {m}" for m in DRAFT_MARKERS if m in text]
    assert not violations, "\n".join(violations)


TENANT_HEADER = re.compile(r"(?i)^x-(zoiko-|zid-)?tenant(-id)?$")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            ids.add(id(body[0].value))
    return ids


def test_tenant_is_never_read_from_caller_input() -> None:
    """INV-13: tenant comes from the authenticated trust context, never a header or body field."""
    sources = [APP / "src", *(p / "src" for p in (BACKEND / "libs").iterdir())]
    violations = []
    for root in sources:
        for py in root.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and TENANT_HEADER.match(node.value)
                ):
                    violations.append(f"{py.relative_to(BACKEND)} reads header {node.value!r}")
    # API request models must not accept a tenant: it is set by authentication.
    for py in MODULES_DIR.glob("*/api/**/*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id in {"tenant_id", "cell_id"}
                    ):
                        violations.append(
                            f"{py.relative_to(BACKEND)}: {node.name}.{item.target.id}"
                        )
    assert not violations, "\n".join(violations)


PURE_SUBMODULES = frozenset({"urllib.parse"})
IO_STDLIB = {
    "os",
    "io",
    "socket",
    "ssl",
    "select",
    "selectors",
    "subprocess",
    "pathlib",
    "shutil",
    "tempfile",
    "sqlite3",
    "http",
    "urllib",
    "asyncio",
    "logging",
    "threading",
    "multiprocessing",
    "smtplib",
    "ftplib",
    "glob",
}


def test_domain_layers_are_pure() -> None:
    """DEC-A2: domain/ is pure Python; I/O lives in infrastructure/ behind ports."""
    # Pure parsing submodules of otherwise I/O packages (urllib.request does I/O; urllib.parse does not).
    allowed_stdlib = set(sys.stdlib_module_names) - IO_STDLIB
    violations = []
    for py in MODULES_DIR.glob("*/domain/**/*.py"):
        own = py.relative_to(MODULES_DIR).parts[0]
        for name in _imports(py):
            top = name.split(".")[0]
            own_domain = name.startswith(f"zid_platform.modules.{own}.domain")
            if not (
                top == "__future__"
                or top in allowed_stdlib
                or own_domain
                or name in PURE_SUBMODULES
            ):
                violations.append(f"{py.relative_to(APP)} imports {name}")
    assert not violations, "\n".join(violations)


def test_published_contracts_match_code() -> None:
    """The committed AuthZEN OpenAPI contract is exactly what the code serves (regenerate to change it)."""
    from zid_platform.contracts_export import AUTHZEN_FILE, authzen_contract, render

    assert AUTHZEN_FILE.read_text(encoding="utf-8") == render(authzen_contract()), (
        "contract drift: run `python -m zid_platform.contracts_export` and review the diff"
    )
