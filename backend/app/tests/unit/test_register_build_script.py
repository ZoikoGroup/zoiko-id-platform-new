"""The build pipeline's registration script (CR-065): what it sends, what it refuses to send, and that it never prints a secret."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

REPO = Path(__file__).resolve().parents[4]
D = "sha256:" + "d" * 64


def _load() -> ModuleType:
    sys.path.insert(0, str(REPO / "tools" / "scripts"))
    spec = importlib.util.spec_from_file_location("zid_tool_register_build", REPO / "tools" / "scripts" / "register_build.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


T = _load()


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


@pytest.fixture
def key(tmp_path: Path) -> Path:
    k = ec.generate_private_key(ec.SECP256R1())
    nums = k.private_numbers()
    f = tmp_path / "ci.jwk"
    f.write_text(json.dumps({"kty": "EC", "crv": "P-256", "kid": "ci-1", "d": _b64(nums.private_value), "x": _b64(nums.public_numbers.x), "y": _b64(nums.public_numbers.y)}))
    return f


def api(register_status: int = 201, error_code: str | None = None, seen: dict | None = None) -> httpx.Client:
    seen = seen if seen is not None else {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth2/token":
            seen["grant"] = dict(x.split("=", 1) for x in req.content.decode().split("&"))["grant_type"]
            return httpx.Response(200, json={"access_token": "tok-SECRET", "expires_in": 300})
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        if register_status == 201:
            return httpx.Response(201, json={"build_id": "b-1", "review_status": "DRAFT"})
        return httpx.Response(register_status, json={"error": {"code": error_code}})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.example.com")


def run(argv: list[str], http: httpx.Client, key: Path, **kw: object) -> int:
    return T.main(["--api-base", "https://api.example.com", "--client-id", "ci", "--key", str(key), "--name", "Bot 1.4", *argv], http=http, **kw)


def test_it_signs_in_as_an_application_and_registers_exactly_what_it_was_given(key: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict = {}
    assert run(["--digest", D, "--source", "ghcr.io/acme/bot:1.4", "--provenance", "https://ci/run/1"], api(seen=seen), key) == 0
    assert seen["grant"] == "client_credentials" and seen["auth"] == "Bearer tok-SECRET" and seen["path"] == "/v1/machine/builds"
    assert seen["body"] == {"name": "Bot 1.4", "artifact_digest": D, "source_ref": "ghcr.io/acme/bot:1.4", "provenance_ref": "https://ci/run/1"}
    out = capsys.readouterr()
    assert "waiting for a person to approve" in out.out
    assert "tok-SECRET" not in out.out + out.err and "BEGIN" not in out.out + out.err


def test_it_never_prints_the_token_or_the_key_even_when_refused(key: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["--digest", D], api(403, "FORBIDDEN"), key) == 1
    out = capsys.readouterr()
    assert "HTTP 403 FORBIDDEN" in out.err and "tok-SECRET" not in out.out + out.err
    assert json.loads(key.read_text())["d"] not in out.out + out.err


def test_a_rerun_of_the_same_build_is_only_an_error_when_asked_to_be(key: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["--digest", D], api(409, "BUILD_EXISTS"), key) == 1
    assert run(["--digest", D, "--allow-existing"], api(409, "BUILD_EXISTS"), key) == 0
    assert run(["--digest", D, "--allow-existing"], api(409, "SOMETHING_ELSE"), key) == 1, "only 'already registered' is forgiven"


def test_a_bad_digest_is_refused_before_anything_is_sent(key: Path) -> None:
    seen: dict = {}
    assert run(["--digest", "sha256:short"], api(seen=seen), key) == 2
    assert seen == {}


def test_the_digest_can_be_read_from_a_local_image() -> None:
    calls: list[list[str]] = []

    def fake(args):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if "{{.Id}}" in args:
            return D
        return f"ghcr.io/other/thing@sha256:{'1' * 64}\nghcr.io/acme/bot@{D}\n"

    assert T.digest_from_image("image-id", "acme/bot:1.4", fake) == D
    assert T.digest_from_image("repo-digest", "ghcr.io/acme/bot:1.4", fake) == D, "the digest recorded for THIS repository, not another"
    assert calls[0][:2] == ["docker", "inspect"]


def test_a_local_image_with_no_registry_digest_says_to_push_it_first() -> None:
    with pytest.raises(ValueError, match="push it first"):
        T.digest_from_image("repo-digest", "acme/bot:1.4", lambda a: "")


def test_it_cannot_do_more_than_register() -> None:
    src = (REPO / "tools" / "scripts" / "register_build.py").read_text(encoding="utf-8")
    assert ":approve" not in src and ":revoke" not in src and "/blueprints" not in src
