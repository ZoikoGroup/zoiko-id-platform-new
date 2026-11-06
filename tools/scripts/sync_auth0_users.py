"""Sync a company's Auth0 users into Zoiko iD through the SCIM endpoint (development and operations tool).

Reads the users of an Auth0 tenant with a read-only machine-to-machine application, and creates, updates and deactivates the matching
people in one Zoiko iD provisioning source. Nothing is written unless `--apply` is given: the default run reads both sides and prints
what it would do, with e-mail addresses masked.

    python tools/scripts/sync_auth0_users.py --check                      # can this machine sign in to the SCIM source? (no Auth0 needed)
    python tools/scripts/sync_auth0_users.py                              # preview: what would change
    python tools/scripts/sync_auth0_users.py --apply                      # do it

What it needs (all passed in, none stored here):
  - the Auth0 tenant domain and the sync application's client id; its client secret is read from `ZID_SECRET__<REF>` (REF upper-cased,
    "-" -> "_", default `auth0-sync` -> `ZID_SECRET__AUTH0_SYNC`) in the environment or backend/.env, and never printed;
  - the SCIM source's OAuth client id and the PRIVATE key file made with it (the file the dialog offered to save), used only to sign
    the token request (private_key_jwt); it is never sent anywhere.

Each Auth0 user becomes one SCIM user: userName = e-mail (the Auth0 user id when there is none), externalId = the Auth0 user id
(`auth0|...`, the same value Auth0 puts in the sign-in `sub`), active = not blocked. A person already in the source is matched by
externalId first, then by userName. People in the source that Auth0 no longer lists are deactivated, never deleted.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import site
import sys
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "backend" / ".venv" / "Lib" / "site-packages", ROOT / "backend" / ".venv" / "lib"):
    if extra.exists():
        site.addsitedir(str(extra))
sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa: E402
from localenv import load_backend_env  # noqa: E402

load_backend_env()

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
AUTH0_PAGE = 100
AUTH0_WINDOW = 1000  # Auth0 lists at most this many users through the paged endpoint
SCIM_PAGE = 100


# ------------------------------------------------------------------------------------------------ small helpers


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mask(email: str | None) -> str:
    if not email or "@" not in email:
        return email or "(no e-mail)"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def fail(message: str) -> NoReturn:
    print(f"sync_auth0_users: {message}", file=sys.stderr)
    raise SystemExit(2)


def checked(res: httpx.Response, what: str) -> httpx.Response:
    if res.status_code >= 400:
        body = res.text[:300].replace("\n", " ")
        fail(f"{what} failed: HTTP {res.status_code} {body}")
    return res


# ------------------------------------------------------------------------------------- Zoiko iD: sign in and talk SCIM


def signed_assertion(client_id: str, audience: str, key_file: Path) -> str:
    """RFC 7523 client assertion: signed with the connector's private key, valid for one minute, used once."""
    jwk = json.loads(key_file.read_text(encoding="utf-8"))
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" not in jwk:
        fail("the key file must be the private EC P-256 key (a JWK with a `d` member)")
    key = ec.derive_private_key(int.from_bytes(unb64(jwk["d"]), "big"), ec.SECP256R1())
    now = int(time.time())
    head = {"alg": "ES256", "typ": "JWT", "kid": jwk["kid"]}
    claims = {"iss": client_id, "sub": client_id, "aud": audience, "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex}
    signing_input = f"{b64(json.dumps(head, separators=(',', ':')).encode())}.{b64(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class Scim:
    def __init__(self, base: str, client_id: str, key_file: Path) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=30)
        token_url = f"{self.base}/oauth2/token"
        res = checked(
            self.http.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "scope": "scim:provision",
                    "client_id": client_id,
                    "client_assertion_type": ASSERTION_TYPE,
                    "client_assertion": signed_assertion(client_id, token_url, key_file),
                },
            ),
            "Zoiko iD token request",
        )
        self.headers = {"authorization": f"Bearer {res.json()['access_token']}", "content-type": "application/scim+json"}

    def url(self, path: str) -> str:
        return f"{self.base}/scim/v2{path}"

    def users(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        start = 1
        while True:
            body = checked(self.http.get(self.url(f"/Users?startIndex={start}&count={SCIM_PAGE}"), headers=self.headers), "SCIM list").json()
            found += body.get("Resources", [])
            start += SCIM_PAGE
            if not body.get("Resources") or len(found) >= body.get("totalResults", 0):
                return found

    def create(self, user: dict[str, Any]) -> None:
        checked(self.http.post(self.url("/Users"), headers=self.headers, json=user), "SCIM create")

    def replace(self, resource_id: str, user: dict[str, Any]) -> None:
        checked(self.http.put(self.url(f"/Users/{resource_id}"), headers=self.headers, json=user), "SCIM update")

    def set_active(self, resource_id: str, active: bool) -> None:
        patch = {"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": active}]}
        checked(self.http.patch(self.url(f"/Users/{resource_id}"), headers=self.headers, json=patch), "SCIM deactivate")


# ------------------------------------------------------------------------------------------------- Auth0: list users


def auth0_users(domain: str, client_id: str, secret: str) -> list[dict[str, Any]]:
    http = httpx.Client(timeout=30)
    audience = f"https://{domain}/api/v2/"
    token = checked(
        http.post(
            f"https://{domain}/oauth/token",
            json={"client_id": client_id, "client_secret": secret, "audience": audience, "grant_type": "client_credentials"},
        ),
        "Auth0 token request",
    ).json()
    if "read:users" not in str(token.get("scope", "")).split():
        fail(f"the Auth0 application was not granted read:users (granted: {token.get('scope')!r})")
    wide = set(str(token.get("scope", "")).split()) - {"read:users"}
    if wide:
        fail(f"the Auth0 application holds more than read:users ({len(wide)} extra permissions). Remove them, then run again.")
    headers = {"authorization": f"Bearer {token['access_token']}"}
    fields = "user_id,email,name,given_name,family_name,blocked,email_verified"
    users: list[dict[str, Any]] = []
    page = 0
    while True:
        body = checked(
            http.get(
                f"{audience}users",
                headers=headers,
                params={"per_page": AUTH0_PAGE, "page": page, "include_totals": "true", "fields": fields, "include_fields": "true"},
            ),
            "Auth0 user list",
        ).json()
        users += body["users"]
        if len(users) >= body["total"] or not body["users"]:
            break
        page += 1
    if body["total"] > AUTH0_WINDOW:
        print(f"warning: Auth0 has {body['total']} users but its paged list covers only the first {AUTH0_WINDOW}; use an export for the rest.")
    return users


# ------------------------------------------------------------------------------------------------------- the mapping


def to_scim(u: dict[str, Any]) -> dict[str, Any]:
    email = u.get("email")
    user: dict[str, Any] = {
        "schemas": [USER_SCHEMA],
        "userName": email or u["user_id"],
        "externalId": u["user_id"],
        "active": not u.get("blocked", False),
    }
    if u.get("name"):
        user["displayName"] = u["name"]
    if u.get("given_name") or u.get("family_name"):
        user["name"] = {k: v for k, v in (("givenName", u.get("given_name")), ("familyName", u.get("family_name"))) if v}
    if email:
        user["emails"] = [{"value": email, "primary": True}]
    return user


def differs(want: dict[str, Any], have: dict[str, Any]) -> bool:
    """Does the person in Zoiko iD need an update to match what Auth0 says?"""
    have_email = next((e.get("value") for e in have.get("emails", []) if e.get("primary")), None)
    want_email = next((e["value"] for e in want.get("emails", []) if e.get("primary")), None)
    return (
        want["userName"] != have.get("userName")
        or want["active"] != have.get("active", True)
        or want.get("displayName") != have.get("displayName")
        or want_email != have_email
        or want.get("name", {}).get("givenName") != have.get("name", {}).get("givenName")
        or want.get("name", {}).get("familyName") != have.get("name", {}).get("familyName")
    )


def plan(auth0: list[dict[str, Any]], zoiko: list[dict[str, Any]]) -> dict[str, list[Any]]:
    by_external = {r["externalId"]: r for r in zoiko if r.get("externalId")}
    by_name = {r["userName"].lower(): r for r in zoiko}
    out: dict[str, list[Any]] = {"create": [], "update": [], "same": [], "deactivate": []}
    seen: set[str] = set()
    for u in auth0:
        want = to_scim(u)
        have = by_external.get(u["user_id"]) or by_name.get(want["userName"].lower())
        if have is None:
            out["create"].append(want)
            continue
        seen.add(have["id"])
        (out["update"] if differs(want, have) else out["same"]).append((have["id"], want))
    out["deactivate"] = [r for r in zoiko if r["id"] not in seen and r.get("active", True)]
    return out


# ------------------------------------------------------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-base", default="http://localhost:8010", help="the cell's public address (default: the local cell)")
    p.add_argument("--scim-client-id", default="hr-ed5bcaf5", help="the OAuth client of the SCIM source")
    p.add_argument("--scim-key", default=str(Path.home() / "Downloads" / "scim-connector-private-key.json"), help="the private key file for that client")
    p.add_argument("--auth0-domain", default="dev-qogbbz4c306u41k1.us.auth0.com")
    p.add_argument("--auth0-client-id", default="5S1hCXgVJGHWf0jvMTPJjJwIZNtSZHWe", help="the read-only machine-to-machine application")
    p.add_argument("--secret-ref", default="auth0-sync", help="name of the stored client secret (env ZID_SECRET__<NAME>)")
    p.add_argument("--check", action="store_true", help="only sign in to the SCIM source and count its users")
    p.add_argument("--apply", action="store_true", help="write the changes (the default only shows them)")
    args = p.parse_args()

    key_file = Path(args.scim_key)
    if not key_file.is_file():
        fail(f"private key file not found: {key_file}")
    scim = Scim(args.api_base, args.scim_client_id, key_file)
    existing = scim.users()
    print(f"signed in to the SCIM source as {args.scim_client_id}; it holds {len(existing)} user(s)")
    if args.check:
        return 0

    env_name = "ZID_SECRET__" + args.secret_ref.upper().replace("-", "_")
    secret = os.environ.get(env_name)
    if not secret:
        fail(f"the Auth0 client secret is not set: add {env_name}=<secret> to backend/.env (or the environment) and run again")
    users = auth0_users(args.auth0_domain, args.auth0_client_id, secret)
    print(f"Auth0 lists {len(users)} user(s)")

    todo = plan(users, existing)
    for kind in ("create", "update", "deactivate"):
        for item in todo[kind]:
            who = item["userName"] if kind in ("create", "deactivate") else item[1]["userName"]
            print(f"  {kind:<10} {mask(who)}")
    print(f"summary: {len(todo['create'])} to create, {len(todo['update'])} to update, {len(todo['same'])} unchanged, {len(todo['deactivate'])} to deactivate")
    if not args.apply:
        print("nothing was written (preview). Run again with --apply to make these changes.")
        return 0

    for want in todo["create"]:
        scim.create(want)
    for resource_id, want in todo["update"]:
        scim.replace(resource_id, want)
    for r in todo["deactivate"]:
        scim.set_active(r["id"], False)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
