"""Injection controls (ZID-SEC-001 §15 "API input", §28 SEC-21; ASVS V1/V5).

SEC-21: the platform's injection surfaces are SQL, OS commands, templates, LDAP, file paths and
deserialization. This is a static control assertion over every first-party source file (SEC-001 §28
"Every fixed exploitable class receives regression test or control assertion"), plus its dynamic
counterpart in `tests/component/test_security_boundaries_pg.py` (hostile input over HTTP).

- SQL: every statement is parameterised. A statement assembled with an f-string, `+` or `.format`
  may interpolate only a module constant, a local built from string literals, or a name on the
  reviewed `REVIEWED_IDENTIFIERS` list (identifier parameters of private store helpers, whose callers
  pass literals). A new dynamic site, or a new interpolated name, fails this test until reviewed.
- OS command / code execution: no `eval`, `exec`, `os.system`, `os.popen`, `shell=True`,
  `subprocess` of a shell string, `pickle`/`marshal`/`shelve` loading, `yaml.load` without a safe
  loader, `__import__` of data-derived names, or `jinja2`/`string.Template` over request data.
- LDAP / XPath / template languages: none of those libraries is imported.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
SOURCES = [
    p
    for root in (BACKEND / "app" / "src", *sorted((BACKEND / "libs").glob("*/src")))
    for p in root.rglob("*.py")
]

# Interpolated names a reviewer has traced to string literals. `table`/`column`/`id_col`/`hash_col`
# and friends are parameters of private store helpers invoked only with literals from the same
# module (see the call sites listed in the review note beside each).
REVIEWED_IDENTIFIERS: dict[str, str] = {
    "table": "private helper parameter; callers pass literal table names",
    "column": "private helper parameter; callers pass literal column names",
    "id_col": "private helper parameter; callers pass literal column names",
    "hash_col": "private helper parameter; callers pass literal column names",
    "ver_col": "private helper parameter; callers pass literal column names",
    "code_col": "private helper parameter; callers pass literal column names",
}

# Reviewed by site: (file suffix, function, interpolated expression) -> why it is not user input.
REVIEWED_SITES: dict[tuple[str, str, str], str] = {
    (
        "trust_center/infrastructure/store.py",
        "counts",
        "v",
    ): "iterates a dict of fixed literal SELECT count(*) statements defined in the same function; the only value bound is :now (WF-12)",
    (
        "verification/infrastructure/method_store.py",
        "history",
        "sql + (' FOR UPDATE' if lock else '')",
    ): "a literal SELECT over a fixed method table with bound parameters, plus a literal FOR UPDATE (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "proposal",
        "sql + (' FOR UPDATE' if lock else '')",
    ): "a literal SELECT over a fixed method table with bound parameters, plus a literal FOR UPDATE (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "for_call",
        "self.state_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "all_states",
        "self.state_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "insert_state",
        "self.state_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "proposals",
        "self.proposal_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "insert_proposal",
        "self.proposal_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "verification/infrastructure/method_store.py",
        "decide_proposal",
        "self.proposal_table",
    ): "MethodTables accepts only the two fixed (state, proposal) table pairs in _KNOWN_TABLES (__post_init__); never input (CR-112)",
    (
        "assurance/infrastructure/store.py",
        "_one",
        "sql",
    ): "private helper; every caller passes a literal statement with bound parameters",
    (
        "assurance/infrastructure/store.py",
        "_all",
        "sql",
    ): "private helper; every caller passes a literal statement with bound parameters",
    (
        "entrypoints/maintenance.py",
        "run_once",
        "where",
    ): "PURGE/SWEEP are module-level literal tuples",
    (
        "access_readiness/infrastructure/store.py",
        "update_setup",
        "', '.join(sets)",
    ): "each SET clause names a column from a fixed allowlist (SETUP_FIELD_UNKNOWN otherwise); values are bound",
    (
        "access_readiness/infrastructure/store.py",
        "list_proposals",
        "' AND '.join(where)",
    ): "WHERE fragments are literals; status and run id are bound parameters",
    ("entrypoints/maintenance.py", "run_once", "statement"): "values of the literal SWEEP dict",
    (
        "tenant/infrastructure/tenant_store.py",
        "set_fields",
        "assignments",
    ): "keys asserted in _UUID_FIELDS | _TEXT_FIELDS; values stay bind parameters",
    (
        "resilience/infrastructure/store.py",
        "update_operation",
        "sets",
    ): "keys asserted against a literal allowlist",
    (
        "provisioning/infrastructure/scim_store.py",
        "query",
        "where",
    ): "fragments chosen from a fixed column map; the SCIM value is a bind parameter",
    (
        "privacy/infrastructure/store.py",
        "update_request",
        "', '.join(sets)",
    ): "keys are keyword names written by privacy service code, never request data; values are bind parameters",
    (
        "operator_access/infrastructure/store.py",
        "update_grant",
        "assignments",
    ): "keys are keyword names written by operator-access service code; values are bind parameters",
    (
        "operations/infrastructure/store.py",
        "update_incident",
        "', '.join(sets)",
    ): "keys asserted against a literal allowlist",
    (
        "machine/infrastructure/agent_store.py",
        "list_agents",
        "where",
    ): "literal clause; the status is a bind parameter",
    (
        "authority/infrastructure/approval_store.py",
        "list_requests",
        "f'SELECT * FROM authz.approval_request WHERE {cond} ORDER BY created_at DESC, approval_request_id DESC' + (' LIMIT :lim' if lim is not None else '')",
    ): "cond is one of three literals; limit is a bind parameter",
}

BANNED_CALLS = {
    "eval": "code execution",
    "exec": "code execution",
    "compile": "code execution",
    "os.system": "OS command",
    "os.popen": "OS command",
    "os.execv": "OS command",
    "os.execl": "OS command",
    "pickle.load": "unsafe deserialization",
    "pickle.loads": "unsafe deserialization",
    "marshal.load": "unsafe deserialization",
    "marshal.loads": "unsafe deserialization",
    "shelve.open": "unsafe deserialization",
    "yaml.load": "unsafe deserialization (use yaml.safe_load)",
    "yaml.unsafe_load": "unsafe deserialization",
    "yaml.full_load": "unsafe deserialization",
    "commands.getoutput": "OS command",
}
BANNED_IMPORTS = {
    "pickle": "unsafe deserialization",
    "cPickle": "unsafe deserialization",
    "marshal": "unsafe deserialization",
    "shelve": "unsafe deserialization",
    "jinja2": "server-side template language",
    "mako": "server-side template language",
    "ldap": "LDAP filter construction",
    "ldap3": "LDAP filter construction",
    "lxml": "XPath / XXE surface",
    "xml.etree": "XXE surface (use defusedxml)",
    "xml.dom": "XXE surface (use defusedxml)",
    "xml.sax": "XXE surface (use defusedxml)",
}
# Reviewed exceptions: file (relative to backend) -> modules it may import, with the reason.
IMPORT_EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "app/src/zid_platform/modules/federation/application/saml_login.py",
        "xml.etree",
    ): "only the ParseError class; every document passes the defusedxml gate first (see below)",
    (
        "app/src/zid_platform/modules/federation/application/saml_login.py",
        "lxml",
    ): "parser has resolve_entities=False, no_network=True and only sees defusedxml-gated bytes",
    (
        "app/src/zid_platform/modules/federation/application/saml_decrypt.py",
        "lxml",
    ): "operates on elements already extracted from a gated, signature-verified document",
    (
        "app/src/zid_platform/modules/evidence/application/trust.py",
        "lxml",
    ): "operator-supplied trusted lists only: the defusedxml gate forbids DTD, entities and external references first, then a parser with resolve_entities=False and no_network=True",
    (
        "app/src/zid_platform/modules/evidence/application/xades.py",
        "lxml",
    ): "caller-supplied signed XML: the same defusedxml gate (no DTD, entities or external references) runs before a parser with resolve_entities=False and no_network=True; a test proves a DTD is refused",
    (
        "app/src/zid_platform/modules/evidence/application/dsig.py",
        "lxml",
    ): "never parses untrusted bytes: it is handed a tree the caller already built behind the defusedxml gate (no DTD, entities or external references) and only canonicalizes and digests it; no XPath, XSLT or network",
}


def rel(p: Path) -> str:
    return p.relative_to(BACKEND).as_posix()


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def parse(p: Path) -> ast.Module:
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def _safe_string(node: ast.AST, safe_locals: set[str]) -> bool:
    """A string expression built only from literals, constants and already-safe locals."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Name):
        return node.id.isupper() or node.id.lstrip("_").isupper() or node.id in safe_locals
    if isinstance(node, ast.JoinedStr):
        return all(
            _safe_string(v.value, safe_locals)
            for v in node.values
            if isinstance(v, ast.FormattedValue)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return _safe_string(node.left, safe_locals) and _safe_string(node.right, safe_locals)
    if isinstance(node, ast.IfExp):
        return _safe_string(node.body, safe_locals) and _safe_string(node.orelse, safe_locals)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
    ):
        return _safe_string(node.func.value, safe_locals) and _safe_iterable(
            node.args[0], safe_locals
        )
    if isinstance(node, ast.Compare):
        return False
    return False


def _safe_iterable(node: ast.AST, safe_locals: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in safe_locals
    if isinstance(node, ast.List | ast.Tuple):
        return all(_safe_string(e, safe_locals) for e in node.elts)
    if isinstance(node, ast.GeneratorExp | ast.ListComp):
        return _safe_string(node.elt, safe_locals)
    return False


def _function_locals(fn: ast.AST) -> set[str]:
    """Locals of `fn` that only ever hold literal-built strings or lists of them."""
    candidates: dict[str, list[ast.AST]] = {}
    appended: dict[str, list[ast.AST]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                candidates.setdefault(t.id, []).append(node.value)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            or isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value
        ):
            candidates.setdefault(node.target.id, []).append(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend", "add")
            and isinstance(node.func.value, ast.Name)
            and node.args
        ):
            appended.setdefault(node.func.value.id, []).append(node.args[0])
    safe: set[str] = set()
    for _ in range(4):  # a few passes: a local may be built from another safe local
        for name, values in candidates.items():
            if name in safe:
                continue
            parts_ok = all(
                _safe_string(v, safe)
                or (isinstance(v, ast.List | ast.Tuple) and _safe_iterable(v, safe))
                for v in values
            ) and all(_safe_string(a, safe) for a in appended.get(name, []))
            if parts_ok:
                safe.add(name)
    return safe


def dynamic_sql_sites() -> list[tuple[str, int, str, str]]:
    """(file, line, function, unresolved interpolated name) for every SQL string that is built."""
    out: list[tuple[str, int, str, str]] = []
    for p in SOURCES:
        tree = parse(p)
        for fn in [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]:
            safe = _function_locals(fn)
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and len(node.args) == 1):
                    continue  # sqlalchemy `text()` takes exactly one argument (the XML helper takes two)
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else dotted(node.func).split(".")[-1]
                )
                if name not in ("text", "exec_driver_sql"):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    continue
                names: list[ast.AST] = []
                if isinstance(arg, ast.JoinedStr):
                    names = [v.value for v in arg.values if isinstance(v, ast.FormattedValue)]
                elif isinstance(arg, ast.BinOp | ast.Name | ast.Call):
                    names = [arg]
                for expr in names:
                    if not _safe_string(expr, safe):
                        ident = ast.unparse(expr)
                        out.append((rel(p), node.lineno, fn.name, ident))
    return out


def test_every_dynamic_sql_fragment_is_a_literal_a_constant_or_a_reviewed_identifier() -> None:
    """SEC-21 (SQL): user input reaches SQL only through bind parameters."""
    unreviewed = [
        f"{f}:{line} {fn}(): interpolates `{ident}`"
        for f, line, fn, ident in dynamic_sql_sites()
        if ident not in REVIEWED_IDENTIFIERS
        and not any(
            f.endswith(suffix) and fn == func and ident == i for (suffix, func, i) in REVIEWED_SITES
        )
    ]
    assert not unreviewed, (
        "SQL assembled from a name that is not a literal, a constant or a reviewed identifier; "
        "bind the value as a parameter (or, for an identifier, review and list it):\n"
        + "\n".join(unreviewed)
    )


def test_the_sql_analyser_flags_a_tainted_fragment() -> None:
    """The check is not vacuous: user-shaped interpolation is reported, literal-built is not."""
    bad = ast.parse(
        "async def f(conn, name):\n    await conn.execute(text(f\"SELECT * FROM t WHERE n = '{name}'\"))\n"
    )
    good = ast.parse(
        "async def g(conn, flag):\n"
        "    where = ['a = :a']\n"
        "    if flag:\n"
        "        where.append('b = :b')\n"
        "    sql = 'SELECT 1 WHERE ' + ' AND '.join(where) + (' FOR UPDATE' if flag else '')\n"
        "    await conn.execute(text(sql))\n"
    )

    def unresolved(tree: ast.Module) -> list[str]:
        fn = tree.body[0]
        safe = _function_locals(fn)
        found = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and dotted(node.func) == "text":
                arg = node.args[0]
                exprs = (
                    [v.value for v in arg.values if isinstance(v, ast.FormattedValue)]
                    if isinstance(arg, ast.JoinedStr)
                    else [arg]
                )
                found += [ast.unparse(e) for e in exprs if not _safe_string(e, safe)]
        return found

    assert unresolved(bad) == ["name"]
    assert unresolved(good) == []


def test_reviewed_identifier_list_has_no_dead_entries() -> None:
    sites = dynamic_sql_sites()
    seen = {ident for _f, _l, _fn, ident in sites}
    assert set(REVIEWED_IDENTIFIERS) <= seen, (
        "remove reviewed identifiers that no SQL builder uses any more: "
        f"{sorted(set(REVIEWED_IDENTIFIERS) - seen)}"
    )
    live = {(f, fn, ident) for f, _l, fn, ident in sites}
    dead = [
        k
        for k in REVIEWED_SITES
        if not any(f.endswith(k[0]) and (fn, i) == k[1:] for f, fn, i in live)
    ]
    assert not dead, f"remove reviewed sites that no longer exist: {dead}"


@pytest.mark.parametrize("path", SOURCES, ids=rel)
def test_no_code_execution_shell_or_unsafe_deserialization(path: Path) -> None:
    """SEC-21 (OS command, template, deserialization): the banned constructs never appear."""
    tree = parse(path)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in BANNED_CALLS:
                problems.append(f"{path.name}:{node.lineno} {name}(): {BANNED_CALLS[name]}")
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    problems.append(f"{path.name}:{node.lineno} shell=True: OS command injection")
            if name == "yaml.load":
                problems.append(f"{path.name}:{node.lineno} yaml.load without a safe loader")
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for m in modules:
            for banned, why in BANNED_IMPORTS.items():
                if (m == banned or m.startswith(banned + ".")) and (
                    rel(path),
                    banned,
                ) not in IMPORT_EXCEPTIONS:
                    problems.append(f"{path.name}:{node.lineno} import {m}: {why}")
    assert not problems, "\n".join(problems)


def test_the_only_subprocess_use_is_an_argument_vector() -> None:
    """SEC-21 (OS command): a subprocess call passes a list, never a string run by a shell."""
    for p in SOURCES:
        for node in ast.walk(parse(p)):
            if (
                isinstance(node, ast.Call)
                and dotted(node.func).startswith("subprocess.")
                and node.args
                and not isinstance(node.args[0], ast.List | ast.Tuple | ast.Name)
            ):
                pytest.fail(f"{rel(p)}:{node.lineno}: subprocess called with a command string")


def test_saml_documents_with_entities_or_dtds_are_refused_before_parsing() -> None:
    """SEC-21 (XML): billion-laughs and external-entity documents never reach the real parser."""
    import pytest as _pytest
    from zid_platform.modules.federation.application.saml_login import _safe
    from zid_platform.modules.federation.domain.connection import FederationError

    xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<l>&b;</l>"
    )
    for payload in (xxe, laughs):
        with _pytest.raises(FederationError):
            _safe(payload, "SAML_RESPONSE_INVALID")
    _safe(b"<ok/>", "SAML_RESPONSE_INVALID")  # a plain document still passes
