"""Listing an image's versions so a person picks one instead of typing it: what is returned, and the same guard rails as the digest lookup."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from zid_platform.modules.machine.infrastructure import registry
from zid_platform.modules.machine.infrastructure.registry import RegistryError

PUBLIC = lambda host: {"8.8.8.8"}


def run(image: str, handler: Any, *, allowed: tuple[str, ...] = ("ghcr.io",)) -> registry.Versions:
    return asyncio.run(
        registry.list_tags(
            image, allowed=allowed, transport=httpx.MockTransport(handler), resolver=PUBLIC
        )
    )


def fails(image: str, handler: Any, code: str, **kw: Any) -> None:
    with pytest.raises(RegistryError) as e:
        run(image, handler, **kw)
    assert e.value.code == code, (e.value.code, str(e.value))


def tags(*names: str, link: str | None = None) -> httpx.Response:
    return httpx.Response(
        200, json={"name": "acme/bot", "tags": list(names)}, headers={"link": link} if link else {}
    )


def test_a_public_repositorys_versions_are_listed() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return tags("1.0.0", "1.1.0", "latest")

    got = run("ghcr.io/acme/bot", handler)
    assert (got.host, got.repository, got.tags) == (
        "ghcr.io",
        "acme/bot",
        ("1.0.0", "1.1.0", "latest"),
    )
    assert seen == ["https://ghcr.io/v2/acme/bot/tags/list?n=1000"]


def test_a_tag_on_the_name_is_ignored_and_a_repository_with_no_tags_is_empty() -> None:
    assert (
        run(
            "ghcr.io/acme/bot:1.4",
            lambda r: httpx.Response(200, json={"name": "acme/bot", "tags": None}),
        ).tags
        == ()
    )


def test_signatures_and_attestations_are_not_versions_of_the_software() -> None:
    got = run(
        "ghcr.io/acme/bot",
        lambda r: tags(
            "1.0.0", "sha256-" + "a" * 64 + ".sig", "sha256-" + "b" * 64 + ".att", "sha-abc123"
        ),
    )
    assert got.tags == ("1.0.0", "sha-abc123")


def test_a_token_is_fetched_for_a_public_image_and_used() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if "authorization" not in req.headers:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:acme/bot:pull"'
                },
            )
        assert req.headers["authorization"] == "Bearer t"
        return tags("2.0.0")

    assert run("ghcr.io/acme/bot", handler).tags == ("2.0.0",)


def test_more_pages_are_followed_up_to_a_limit_and_only_on_the_same_registry() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path + "?" + req.url.query.decode())
        n = len(calls)
        return tags(f"v{n}", link=f'</v2/acme/bot/tags/list?last=v{n}&n=1000>; rel="next"')

    got = run("ghcr.io/acme/bot", handler)
    assert got.tags == tuple(
        f"v{i}" for i in range(1, registry.MAX_TAG_PAGES + 1)
    )  # stops at the page limit
    assert len(calls) == registry.MAX_TAG_PAGES


def test_a_next_link_to_another_address_is_not_followed() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return tags("1.0.0", link='<https://evil.example/v2/acme/bot/tags/list>; rel="next"')

    assert run("ghcr.io/acme/bot", handler).tags == ("1.0.0",)
    assert len(calls) == 1


def test_with_no_registries_named_it_is_off_and_nothing_is_contacted() -> None:
    called: list[str] = []

    def never(req: httpx.Request) -> httpx.Response:
        called.append(str(req.url))
        return tags("1")

    fails("ghcr.io/acme/bot", never, "REGISTRY_LOOKUP_DISABLED", allowed=())
    assert called == []


def test_only_named_registries_are_contacted() -> None:
    fails("evil.example.com/x/y", lambda r: tags("1"), "REGISTRY_NOT_ALLOWED")


def test_a_private_address_is_refused() -> None:
    with pytest.raises(RegistryError) as e:
        asyncio.run(
            registry.list_tags(
                "ghcr.io/acme/bot",
                allowed=("ghcr.io",),
                transport=httpx.MockTransport(lambda r: tags("1")),
                resolver=lambda h: {"10.0.0.5"},
            )
        )
    assert e.value.code == "REGISTRY_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(404), "IMAGE_NOT_FOUND"),
        (httpx.Response(403), "REGISTRY_AUTH_REQUIRED"),
        (
            httpx.Response(302, headers={"location": "https://evil.example/"}),
            "REGISTRY_UNREACHABLE",
        ),
        (httpx.Response(500), "REGISTRY_UNREACHABLE"),
        (httpx.Response(200, content=b"not json"), "REGISTRY_UNREACHABLE"),
        (httpx.Response(200, content=b"x" * (registry.MAX_TAGS_BODY + 1)), "REGISTRY_UNREACHABLE"),
    ],
)
def test_every_failure_is_named_and_none_is_guessed_around(
    response: httpx.Response, code: str
) -> None:
    fails("ghcr.io/acme/bot", lambda r: response, code)


def test_a_registry_that_does_not_answer_is_reported() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    fails("ghcr.io/acme/bot", down, "REGISTRY_UNREACHABLE")


def test_a_listing_that_takes_too_long_is_cut_off_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "TAGS_DEADLINE_S", 0.05)

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1)
            return tags("1")

    with pytest.raises(RegistryError) as e:
        asyncio.run(
            registry.list_tags(
                "ghcr.io/acme/bot", allowed=("ghcr.io",), transport=Slow(), resolver=PUBLIC
            )
        )
    assert e.value.code == "REGISTRY_UNREACHABLE" and "too long" in str(e.value)
