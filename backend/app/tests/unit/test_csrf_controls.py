"""Browser-origin controls (ZID-SEC-001 §15, §28: SEC-20 CORS / CSRF).

The API is bearer-token authenticated, so a page on another site cannot make the browser attach credentials to
a call. The only ambient credential is the `__Host-zid_session` cookie, and every state-changing route that
reads it refuses a request whose Origin is not one of the governed origins. No CORS allow-list exists, so a
browser never lets a foreign page read a response either.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import zid_platform
from zid_platform.main import create_app
from zid_platform.modules.session.api.oauth import _origin_ok
from zid_platform.settings import Settings

SRC = Path(zid_platform.__file__).parent
STATE_CHANGING = {"post", "put", "patch", "delete"}
ORIGIN_CHECKS = {"_check_origin", "_origin_ok"}


def _request(
    origin: str | None, *, allowed: tuple[str, ...] | None = ("https://id.example",)
) -> Any:
    settings = SimpleNamespace(all_origins=allowed) if allowed is not None else None
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(webauthn_settings=settings)),
        headers={} if origin is None else {"origin": origin},
    )


def test_only_a_governed_origin_passes_the_cookie_origin_check() -> None:
    """SEC-20: a foreign site, a missing Origin, `null` and a lookalike are all refused."""
    assert _origin_ok(_request("https://id.example"))
    for origin in (
        "https://evil.example",
        "https://id.example.evil.example",
        "http://id.example",
        "null",
        "",
        None,
    ):
        assert not _origin_ok(_request(origin)), origin
    assert not _origin_ok(
        _request("https://id.example", allowed=None)
    )  # no governed list: nothing passes


def _functions(path: Path) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)]


def _route_methods(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    return {
        d.func.attr
        for d in fn.decorator_list
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and isinstance(d.func.value, ast.Name)
        and d.func.value.id == "router"
    }


def _mentions(fn: ast.AST, names: set[str]) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Attribute) and n.attr in names)
        or (isinstance(n, ast.Constant) and n.value == "CSRF_ORIGIN_REJECTED")
        or (isinstance(n, ast.Constant) and n.value == "origin")
        for n in ast.walk(fn)
    )


def _reads_cookie(fn: ast.AST, cookie_helpers: set[str]) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr == "cookies")
        or (isinstance(n, ast.Name) and n.id in cookie_helpers)
        for n in ast.walk(fn)
    )


def test_every_state_changing_route_that_reads_the_session_cookie_checks_the_origin() -> None:
    """SEC-20: no cookie-authenticated POST/PUT/PATCH/DELETE route can be driven by another site."""
    checked: list[str] = []
    for path in sorted((SRC / "modules").glob("*/api/*.py")):
        fns = _functions(path)
        helpers = {f.name for f in fns if f.name == "_cookie_session"}
        for fn in fns:
            if not (_route_methods(fn) & STATE_CHANGING) or not _reads_cookie(fn, helpers):
                continue
            assert _mentions(fn, ORIGIN_CHECKS), (
                f"{path.name}:{fn.name} reads the cookie without an origin check"
            )
            checked.append(fn.name)
    assert {"consent_decision", "link"} <= set(checked), checked  # the scan finds the known routes


def test_the_session_cookie_is_host_prefixed_secure_httponly_and_not_cross_site() -> None:
    """SEC-20: every cookie the platform sets stays out of cross-site requests and out of scripts."""
    found = 0
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"set_cookie", "delete_cookie"}:
                continue
            found += 1
            kw = {k.arg: k.value for k in node.keywords}
            assert isinstance(kw["secure"], ast.Constant) and kw["secure"].value is True, path
            assert isinstance(kw["samesite"], ast.Constant) and kw["samesite"].value in {
                "lax",
                "strict",
            }
            if node.func.attr == "set_cookie":
                assert isinstance(kw["httponly"], ast.Constant) and kw["httponly"].value is True, (
                    path
                )
                assert isinstance(kw["path"], ast.Constant) and kw["path"].value == "/"
                assert "domain" not in kw  # __Host- cookies carry no Domain attribute
    assert found >= 3
    assert all(
        c.value == "__Host-zid_session"
        for path in SRC.rglob("*.py")
        for c in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(c, ast.Constant)
        and isinstance(c.value, str)
        and c.value.endswith("zid_session")
    )


def test_no_cors_allow_list_is_installed() -> None:
    """SEC-20: the app adds no CORS middleware, so no foreign origin is ever allowed to read a response."""
    app = create_app(Settings())
    assert not [m for m in app.user_middleware if "cors" in m.cls.__name__.lower()]
    for path in SRC.rglob("*.py"):  # nor does any code path (any environment) add one
        assert "CORSMiddleware" not in path.read_text(encoding="utf-8"), path
