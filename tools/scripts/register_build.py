"""Register a software build with Zoiko iD from a build pipeline (CR-065). Standard library plus httpx and cryptography.

    python3 tools/scripts/register_build.py --api-base https://api.example.com --client-id ci-bot-3f9a21c4 --key ci.jwk \\
        --name "Invoice bot $GIT_SHA" --digest sha256:<64 hex> --source ghcr.io/acme/invoice-bot:1.4 --provenance https://ci.example.com/run/123

The digest can instead be read from a local image:  --image-id acme/invoice-bot:1.4   (its image ID)   or   --repo-digest ghcr.io/acme/invoice-bot:1.4
(the digest the registry recorded when it was pushed or pulled).

The pipeline signs in as an application (OAuth client credentials, proven with a key pair it keeps) and needs ONE authority: `machine.build.register`. That is
all it can do. It cannot approve its own build, revoke one, or draft a release: a person approves the build in the console (Agents & tools > Builds), so
a stolen pipeline credential can propose software but never get it run. Exit status: 0 registered (or already registered with --allow-existing),
1 refused or failed, 2 bad arguments. The token and the key are never printed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zid_machine_client import MachineClient, MachineClientError  # noqa: E402

DIGEST = re.compile(r"^sha-?256:[0-9a-f]{64}$")
Runner = Callable[[Sequence[str]], str]


def _docker(args: Sequence[str]) -> str:
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=30, check=False)
    if done.returncode != 0:
        raise ValueError((done.stderr or "docker failed").strip()[:200])
    return done.stdout.strip()


def digest_from_image(kind: str, image: str, run: Runner = _docker) -> str:
    """`image-id`: the local image's ID. `repo-digest`: the registry digest Docker recorded for the name (after a push or a pull)."""
    if kind == "image-id":
        return run(["docker", "inspect", "--format", "{{.Id}}", image])
    out = run(["docker", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", image])
    repo = image.split("@", 1)[0].rsplit(":", 1)[0] if "@" not in image else image.split("@", 1)[0]
    candidates = [line.strip() for line in out.splitlines() if "@" in line]
    for line in candidates:  # prefer the digest recorded for this repository
        if line.split("@", 1)[0].endswith(repo.split("/", 1)[-1]):
            return line.split("@", 1)[1]
    if candidates:
        return candidates[0].split("@", 1)[1]
    raise ValueError(f"{image} has no registry digest yet: push it first, or use --image-id for a local image")


def register(base: str, client: MachineClient, http: httpx.Client, body: dict[str, object]) -> httpx.Response:
    return http.post(f"{base.rstrip('/')}/v1/machine/builds", json=body, headers=client.headers())


def main(argv: Sequence[str] | None = None, *, http: httpx.Client | None = None, run: Runner = _docker) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--api-base", required=True)
    p.add_argument("--client-id", required=True)
    p.add_argument("--key", required=True, type=Path, help="the application's private key (a JWK file)")
    p.add_argument("--name", required=True, help="what people will pick from a list later")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--digest")
    src.add_argument("--image-id", metavar="IMAGE")
    src.add_argument("--repo-digest", metavar="IMAGE")
    p.add_argument("--source", help="where the image is published, e.g. ghcr.io/acme/invoice-bot:1.4")
    p.add_argument("--provenance", help="a link to the build's provenance or signature (recorded, not verified)")
    p.add_argument("--scope", default="machine.build:register", help="the OAuth scope to ask for: the one a build pipeline's client has (default: machine.build:register)")
    p.add_argument("--allow-existing", action="store_true", help="exit 0 if this digest is already registered (a re-run of the same build)")
    args = p.parse_args(argv)

    try:
        digest = args.digest or digest_from_image("image-id" if args.image_id else "repo-digest", args.image_id or args.repo_digest, run)
    except ValueError as e:
        print(f"could not read the digest: {e}", file=sys.stderr)
        return 2
    if not DIGEST.match(digest):
        print("the digest must be sha256: followed by 64 hex characters", file=sys.stderr)
        return 2

    own = http is None
    http = http or httpx.Client(timeout=30)
    try:
        client = MachineClient(args.api_base, args.client_id, args.key, args.scope, http)
        res = register(args.api_base, client, http, {"name": args.name, "artifact_digest": digest, "source_ref": args.source, "provenance_ref": args.provenance})
    except MachineClientError as e:
        print(f"could not sign in: {e.code}", file=sys.stderr)
        return 1
    except httpx.HTTPError as e:
        print(f"could not reach {args.api_base}: {type(e).__name__}", file=sys.stderr)
        return 1
    finally:
        if own:
            http.close()

    code = (res.json().get("error") or {}).get("code") if res.headers.get("content-type", "").startswith("application/json") and res.status_code >= 400 else None
    if res.status_code == 201:
        build = res.json()
        print(f"registered {build['build_id']} ({digest}). It is waiting for a person to approve it in the console: Agents & tools > Builds.")
        return 0
    if res.status_code == 409 and code == "BUILD_EXISTS" and args.allow_existing:
        print(f"{digest} is already registered.")
        return 0
    print(f"refused: HTTP {res.status_code} {code or ''}".strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
