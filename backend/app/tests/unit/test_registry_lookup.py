"""Looking an image's digest up by its name (CR-065): what is read, and every way the server is kept from being pointed somewhere it should not go."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest
from zid_platform.modules.machine.infrastructure import registry
from zid_platform.modules.machine.infrastructure.registry import RegistryError

MANIFEST = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","layers":[]}'
DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
PUBLIC = lambda host: {"8.8.8.8"}  # noqa: E731 - a global address (documentation ranges are not global, and the guard rightly refuses them)


def run(image: str, handler: Any, *, allowed: tuple[str, ...] = ("ghcr.io",), **kw: Any) -> registry.Resolved:
    return asyncio.run(registry.resolve(image, allowed=allowed, transport=httpx.MockTransport(handler), resolver=kw.pop("resolver", PUBLIC), **kw))


def fails(image: str, handler: Any, code: str, **kw: Any) -> None:
    with pytest.raises(RegistryError) as e:
        run(image, handler, **kw)
    assert e.value.code == code, (e.value.code, str(e.value))


def public(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=MANIFEST, headers={"content-type": "application/vnd.oci.image.manifest.v1+json", "docker-content-digest": DIGEST})


# ------------------------------------------------------------------------------------------------ names
@pytest.mark.parametrize(
    ("given", "host", "repo", "ref"),
    [
        ("ghcr.io/acme/invoice-bot:1.4", "ghcr.io", "acme/invoice-bot", "1.4"),
        ("ghcr.io/acme/invoice-bot", "ghcr.io", "acme/invoice-bot", "latest"),
        ("GHCR.IO/acme/bot:v1", "ghcr.io", "acme/bot", "v1"),
        ("nginx", "registry-1.docker.io", "library/nginx", "latest"),
        ("acme/bot:2", "registry-1.docker.io", "acme/bot", "2"),
        ("docker.io/acme/bot:2", "registry-1.docker.io", "acme/bot", "2"),
        ("localhost:5000/bot:1", "localhost:5000", "bot", "1"),
        ("quay.io/org/img@sha256:" + "a" * 64, "quay.io", "org/img", "sha256:" + "a" * 64),
    ],
)
def test_names_are_read_as_docker_reads_them(given: str, host: str, repo: str, ref: str) -> None:
    r = registry.parse(given)
    assert (r.host, r.repository, r.reference) == (host, repo, ref)


@pytest.mark.parametrize(
    "bad",
    ["", "  ", "https://ghcr.io/acme/bot", "ghcr.io/acme/bot:ta g", "ghcr.io/ACME/bot", "ghcr.io/../etc/passwd", "ghcr.io/acme/bot?x=1", "user:pw@ghcr.io/a/b",
     "ghcr.io/acme//bot", "ghcr.io/acme/bot@sha256:short", "ghcr.io/a@b@c", "a" * 500, "ghcr.io/acme/bot:" + "t" * 200, "ghcr.io/acme/bot\\evil", "ghcr.io/acme/bot%2e"],
)
def test_anything_that_is_not_an_image_name_is_refused_before_a_request_is_made(bad: str) -> None:
    with pytest.raises(RegistryError) as e:
        registry.parse(bad)
    assert e.value.code == "IMAGE_NAME_INVALID"


# ------------------------------------------------------------------------------------------------ guard rails
def test_with_no_registries_named_the_feature_is_off_and_nothing_is_contacted() -> None:
    called: list[str] = []

    def never(req: httpx.Request) -> httpx.Response:
        called.append(str(req.url))
        return public(req)

    fails("ghcr.io/acme/bot:1", never, "REGISTRY_LOOKUP_DISABLED", allowed=())
    assert called == []


def test_a_registry_that_was_not_named_is_never_contacted() -> None:
    called: list[str] = []

    def never(req: httpx.Request) -> httpx.Response:
        called.append(str(req.url))
        return public(req)

    fails("evil.example.com/acme/bot:1", never, "REGISTRY_NOT_ALLOWED")
    assert called == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "172.16.0.1", "::1", "fe80::1", "100.64.0.1"])
def test_a_registry_that_resolves_to_a_private_address_is_not_contacted(address: str) -> None:
    called: list[str] = []

    def never(req: httpx.Request) -> httpx.Response:
        called.append(str(req.url))
        return public(req)

    fails("ghcr.io/acme/bot:1", never, "REGISTRY_NOT_ALLOWED", resolver=lambda h: {address})
    assert called == []


def test_a_private_address_is_allowed_only_where_the_operator_named_it_for_that() -> None:
    got = run("localhost:5000/bot:1", public, allowed=("localhost:5000",), private_ok=("localhost:5000",), resolver=lambda h: {"127.0.0.1"})
    assert got.digest == DIGEST


def test_one_address_in_a_mixed_answer_is_enough_to_refuse() -> None:
    fails("ghcr.io/acme/bot:1", public, "REGISTRY_NOT_ALLOWED", resolver=lambda h: {"8.8.8.8", "10.0.0.5"})


def test_a_redirect_is_not_followed() -> None:
    fails("ghcr.io/acme/bot:1", lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "REGISTRY_UNREACHABLE")


# ------------------------------------------------------------------------------------------------ the answer
def test_the_digest_is_the_hash_of_the_manifest_the_registry_sent() -> None:
    got = run("ghcr.io/acme/bot:1.4", public)
    assert got.digest == DIGEST and got.media_type == "application/vnd.oci.image.manifest.v1+json"
    assert (got.host, got.repository, got.reference) == ("ghcr.io", "acme/bot", "1.4")


def test_a_digest_header_that_disagrees_with_the_manifest_is_refused() -> None:
    fails("ghcr.io/acme/bot:1", lambda r: httpx.Response(200, content=MANIFEST, headers={"docker-content-digest": "sha256:" + "0" * 64}), "REGISTRY_UNREACHABLE")


def test_asking_for_a_digest_gets_that_digest_or_an_error() -> None:
    assert run(f"ghcr.io/acme/bot@{DIGEST}", public).digest == DIGEST
    fails("ghcr.io/acme/bot@sha256:" + "1" * 64, public, "REGISTRY_UNREACHABLE")


def test_an_unknown_image_is_not_found_and_a_broken_registry_is_unreachable() -> None:
    fails("ghcr.io/acme/nope:1", lambda r: httpx.Response(404), "IMAGE_NOT_FOUND")
    fails("ghcr.io/acme/bot:1", lambda r: httpx.Response(500), "REGISTRY_UNREACHABLE")

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    fails("ghcr.io/acme/bot:1", down, "REGISTRY_UNREACHABLE")


def test_a_manifest_bigger_than_the_cap_is_refused() -> None:
    fails("ghcr.io/acme/bot:1", lambda r: httpx.Response(200, content=b"x" * (registry.MAX_MANIFEST + 1)), "REGISTRY_UNREACHABLE")


# ------------------------------------------------------------------------------------------------ anonymous token
def token_flow(token_status: int = 200, realm: str = "https://ghcr.io/token") -> Any:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/token":
            seen["token_query"] = dict(req.url.params)
            return httpx.Response(token_status, json={"token": "anon-token"})
        if req.headers.get("authorization") == "Bearer anon-token":
            seen["authed"] = True
            return public(req)
        return httpx.Response(401, headers={"www-authenticate": f'Bearer realm="{realm}",service="ghcr.io",scope="repository:acme/bot:pull"'})

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def test_a_public_image_is_read_with_an_anonymous_token_the_registry_names() -> None:
    h = token_flow()
    assert run("ghcr.io/acme/bot:1", h).digest == DIGEST
    assert h.seen["authed"] and h.seen["token_query"] == {"service": "ghcr.io", "scope": "repository:acme/bot:pull"}


def test_a_private_image_is_reported_as_needing_a_credential_never_guessed_at() -> None:
    fails("ghcr.io/acme/bot:1", token_flow(token_status=401), "REGISTRY_AUTH_REQUIRED")
    fails("ghcr.io/acme/bot:1", lambda r: httpx.Response(401, headers={"www-authenticate": 'Basic realm="x"'}), "REGISTRY_AUTH_REQUIRED")


def test_the_token_service_is_held_to_the_same_rules_as_the_registry() -> None:
    fails("ghcr.io/acme/bot:1", token_flow(realm="http://ghcr.io/token"), "REGISTRY_UNREACHABLE")  # not https
    fails("ghcr.io/acme/bot:1", token_flow(realm="https://attacker.example/token"), "REGISTRY_NOT_ALLOWED")  # not named
    fails("ghcr.io/acme/bot:1", token_flow(realm="https://ghcr.io/token"), "REGISTRY_NOT_ALLOWED", resolver=lambda h: {"10.0.0.1"})  # resolves privately
