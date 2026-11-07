"""Find the digest of a container image from its name, by asking its registry (CR-065; the registry's own HTTP API v2).

A person knows an image as `ghcr.io/acme/invoice-bot:1.4`, not as a sha256. This asks the registry what the name points at today and returns the digest, which
the person then registers as a build for a second person to approve.

The server fetches an address a user supplied, so the guard rails are the point:
  * only registries an operator named (ZID_BUILD_REGISTRIES, exact host names) are ever contacted; the list empty means the feature is off;
  * the host must not resolve to a loopback, private or link-local address (unless the operator names it in ZID_BUILD_REGISTRIES_PRIVATE, for a local registry);
  * https only; redirects are not followed; the token service the registry names is held to the same list;
  * time and size are capped; the digest is computed from the manifest bytes themselves, never taken from a header alone.
Only anonymous access is built. A private image needs a credential the platform does not hold: it is reported as such, not guessed at.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable, Collection
from dataclasses import dataclass

import httpx

MAX_MANIFEST = 4 * 1024 * 1024
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,253}[a-z0-9])?(:\d{1,5})?$")
_REPO = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DOCKER_HUB = {"docker.io", "index.docker.io", "registry-1.docker.io"}


class RegistryError(Exception):
    """`code` is a stable name the API reports; `message` is safe to show a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Reference:
    host: str  # the host to contact (Docker Hub is registry-1.docker.io)
    repository: str
    reference: str  # a tag, or a digest when the name carried one
    display: str  # the name as the person gave it, normalised


@dataclass(frozen=True, slots=True)
class Resolved:
    digest: str
    media_type: str
    host: str
    repository: str
    reference: str


def parse(image: str) -> Reference:
    """`[host[:port]/]repo[:tag][@sha256:…]`. A first component is a host only if it has a dot or a port or is localhost, as Docker reads it."""
    name = image.strip()
    if not name or len(name) > 400 or any(c in name for c in " \t\r\n\\?#&=%") or "://" in name or name.count("@") > 1:
        raise RegistryError("IMAGE_NAME_INVALID", "That is not an image name. Use the form ghcr.io/acme/invoice-bot:1.4")
    digest = None
    if "@" in name:
        name, digest = name.split("@", 1)
        if not DIGEST.match(digest):
            raise RegistryError("IMAGE_NAME_INVALID", "The digest after @ must be sha256: followed by 64 hex characters")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, path = first.lower(), rest
    else:
        host, path = "docker.io", name
    tag = None
    if ":" in path.rsplit("/", 1)[-1]:
        path, tag = path.rsplit(":", 1)
    if not _HOST.match(host) or not _REPO.match(path) or (tag is not None and not _TAG.match(tag)):
        raise RegistryError("IMAGE_NAME_INVALID", "That is not an image name. Use the form ghcr.io/acme/invoice-bot:1.4")
    if host in _DOCKER_HUB:
        shown = host = "registry-1.docker.io"
        if "/" not in path:
            path = f"library/{path}"
        shown = f"docker.io/{path}"
    else:
        shown = f"{host}/{path}"
    ref = digest or tag or "latest"
    return Reference(host, path, ref, f"{shown}{'@' + digest if digest else ':' + (tag or 'latest')}")


Resolver = Callable[[str], Collection[str]]


def _system_resolver(host: str) -> Collection[str]:
    return {i[4][0] for i in socket.getaddrinfo(host.rsplit(":", 1)[0] if host.count(":") == 1 else host, None)}


async def _require_allowed(host: str, allowed: Collection[str], private_ok: Collection[str], resolver: Resolver | None) -> None:
    if host not in allowed:
        raise RegistryError("REGISTRY_NOT_ALLOWED", f"{host} is not a registry this platform is set up to look in")
    try:
        addresses = await asyncio.to_thread(resolver or _system_resolver, host)
    except OSError as e:
        raise RegistryError("REGISTRY_UNREACHABLE", f"{host} could not be found") from e
    if host in private_ok:
        return
    for a in addresses:
        if not ipaddress.ip_address(a).is_global:
            raise RegistryError("REGISTRY_NOT_ALLOWED", f"{host} resolves to a private address, which this platform will not contact")


_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')


async def _token(client: httpx.AsyncClient, challenge: str, allowed: Collection[str], private_ok: Collection[str], resolver: Resolver | None) -> str:
    if not challenge.lower().startswith("bearer "):
        raise RegistryError("REGISTRY_AUTH_REQUIRED", "This registry asks for a credential this platform does not hold (only public images can be looked up)")
    fields = dict(_CHALLENGE.findall(challenge))
    realm = fields.get("realm", "")
    parts = httpx.URL(realm) if realm else None
    if parts is None or parts.scheme != "https" or not parts.host:
        raise RegistryError("REGISTRY_UNREACHABLE", "The registry's token service is not an https address")
    await _require_allowed(parts.host + (f":{parts.port}" if parts.port else ""), allowed, private_ok, resolver)
    params = {k: v for k, v in (("service", fields.get("service")), ("scope", fields.get("scope"))) if v}
    res = await client.get(realm, params=params)
    if res.status_code in (401, 403):
        raise RegistryError("REGISTRY_AUTH_REQUIRED", "The image was not found, or it is not public: looking up a private image needs a credential this platform does not hold")
    if res.status_code != 200:
        raise RegistryError("REGISTRY_UNREACHABLE", f"The registry's token service answered {res.status_code}")
    body = res.json()
    token = body.get("token") or body.get("access_token")
    if not isinstance(token, str) or not token:
        raise RegistryError("REGISTRY_UNREACHABLE", "The registry's token service gave no token")
    return token


async def resolve(
    image: str,
    *,
    allowed: Collection[str],
    private_ok: Collection[str] = (),
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> Resolved:
    if not allowed:
        raise RegistryError("REGISTRY_LOOKUP_DISABLED", "Looking up an image by name is not set up here (no registries are named in ZID_BUILD_REGISTRIES)")
    ref = parse(image)
    await _require_allowed(ref.host, allowed, private_ok, resolver)
    url = f"https://{ref.host}/v2/{ref.repository}/manifests/{ref.reference}"
    headers = {"Accept": ACCEPT, "User-Agent": "zoiko-id-build-lookup/1"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT, follow_redirects=False) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 401:
                token = await _token(client, res.headers.get("www-authenticate", ""), allowed, private_ok, resolver)
                res = await client.get(url, headers={**headers, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise RegistryError("REGISTRY_UNREACHABLE", f"{ref.host} did not answer ({type(e).__name__})") from e
    if res.status_code in (401, 403):
        raise RegistryError("REGISTRY_AUTH_REQUIRED", "The image was not found, or it is not public: looking up a private image needs a credential this platform does not hold")
    if res.status_code == 404:
        raise RegistryError("IMAGE_NOT_FOUND", f"{ref.display} was not found in {ref.host}")
    if 300 <= res.status_code < 400:
        raise RegistryError("REGISTRY_UNREACHABLE", "The registry redirected the request, which is not followed")
    if res.status_code != 200:
        raise RegistryError("REGISTRY_UNREACHABLE", f"{ref.host} answered {res.status_code}")
    body = res.content
    if len(body) > MAX_MANIFEST:
        raise RegistryError("REGISTRY_UNREACHABLE", "The manifest is larger than this platform will read")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    claimed = res.headers.get("docker-content-digest")
    if claimed and claimed != digest:
        raise RegistryError("REGISTRY_UNREACHABLE", "The registry's digest header does not match the manifest it sent")
    if ref.reference.startswith("sha256:") and ref.reference != digest:
        raise RegistryError("REGISTRY_UNREACHABLE", "The registry returned a manifest that is not the digest asked for")
    return Resolved(digest, res.headers.get("content-type", "").split(";")[0], ref.host, ref.repository, ref.reference)
