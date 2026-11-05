"""SCIM 2.0 lifecycle (ZID-AUTH-001 §11, §16; RFC 7643/7644; AUTH-W6, AUTH-11).

SCIM is lifecycle only: it creates and updates principals, never authority (a Group is a set of
membership facts and grants nothing). Deactivation (`active: false`) or deletion is applied as an
AUTHORITATIVE `account.disabled` signal from this source, so it travels the same path as any
other security event: principal SUSPENDED, security revision bumped, every session revoked,
security state recorded and propagated on the priority lane - within the R1/R2/R3 SLOs. A
re-activation restores the principal but never its old sessions. PATCH paths are validated
strictly against the SCIM schema (scim2-models); unknown or immutable paths are refused.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from pydantic import ValidationError
from scim2_models import (
    URN,
    ComplexAttribute,
    Context,
    Extension,
    Group,
    Mutability,
    PatchOp,
    SCIMException,
    User,
)
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
)
from zid_platform.modules.provisioning.infrastructure import scim_store as store
from zid_platform.modules.provisioning.infrastructure.scim_store import Resource, ScimError, Source
from zid_platform.modules.signals.ports import CanonicalSignal, ingest

ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


class _Manager(ComplexAttribute):
    """Only the manager's id is kept: the SCIM client has no `$ref` to give before it knows our ids."""

    value: str | None = None
    display_name: Annotated[str | None, Mutability.read_only] = None


class EnterpriseUser(Extension):
    """The enterprise extension (RFC 7643 §4.3) with the attributes an HR system sends."""

    __schema__ = URN(ENTERPRISE)

    employee_number: str | None = None
    cost_center: str | None = None
    organization: str | None = None
    division: str | None = None
    department: str | None = None
    manager: _Manager | None = None


ScimUser = User[EnterpriseUser]  # core User plus the enterprise extension
# What is kept and echoed (SCIM names). Profile data only: `roles`, `entitlements`, `groups`,
# `x509Certificates`, `password` and the rest are never stored, so SCIM cannot grant authority.
PROFILE_ATTRIBUTES = (
    "name",
    "emails",
    "phoneNumbers",
    "addresses",
    "nickName",
    "title",
    "userType",
    "preferredLanguage",
    "locale",
    "timezone",
    "profileUrl",
)
USER_ATTRIBUTES = (*PROFILE_ATTRIBUTES, ENTERPRISE)
MAX_MULTI = 20  # entries in a multi-valued attribute (emails, phoneNumbers, addresses)


def _invalid(e: ValidationError) -> ScimError:
    errors = e.errors()
    if not errors:
        return ScimError(400, "invalidValue", "invalid request")
    scim_type = "invalidPath" if errors[0]["type"] == "scim_invalidPath" else "invalidValue"
    return ScimError(400, scim_type, str(errors[0]["msg"])[:200])


def parse_user(payload: dict[str, Any], ctx: Context) -> ScimUser:
    try:
        return ScimUser.model_validate(payload, scim_ctx=ctx)
    except ValidationError as e:
        raise _invalid(e) from e


MAX_TEXT = 256  # the width of the scim_resource text columns


def _fit(**values: str | None) -> None:
    """A value wider than its column is a client error (400), not a database CHECK failure."""
    for name, v in values.items():
        if v is not None and len(v) > MAX_TEXT:
            raise ScimError(400, "invalidValue", f"{name} is longer than {MAX_TEXT} characters")


def _bounded(name: str, value: Any, depth: int = 0) -> None:
    """Every kept string fits MAX_TEXT and every list MAX_MULTI; nesting stays shallow (400 invalidValue)."""
    if depth > 3:
        raise ScimError(400, "invalidValue", f"{name} is nested too deeply")
    if isinstance(value, str):
        _fit(**{name: value})
    elif isinstance(value, list):
        if len(value) > MAX_MULTI:
            raise ScimError(400, "invalidValue", f"{name} has more than {MAX_MULTI} values")
        for v in value:
            _bounded(name, v, depth + 1)
    elif isinstance(value, dict):
        for k, v in value.items():
            _bounded(f"{name}.{k}", v, depth + 1)


def _attrs(user: ScimUser) -> dict[str, Any]:
    dumped = user.model_dump(scim_ctx=Context.RESOURCE_CREATION_REQUEST, mode="json")
    kept = {k: dumped[k] for k in USER_ATTRIBUTES if dumped.get(k) not in (None, {}, [])}
    manager = (kept.get(ENTERPRISE) or {}).get("manager")
    if isinstance(manager, dict):  # the id is all that is stored; `$ref` and the name are derived
        kept[ENTERPRISE]["manager"] = {"value": manager.get("value")}
    for k, v in kept.items():
        _bounded(k, v)
    return kept


async def _check_manager(conn: AsyncConnection, src: Source, attrs: dict[str, Any]) -> None:
    """A manager is another live User of this source."""
    manager = (attrs.get(ENTERPRISE) or {}).get("manager") or {}
    if manager.get("value"):
        try:
            await store.load(conn, src.source_id, "User", str(manager["value"]))
        except ScimError as e:
            raise ScimError(
                400, "invalidValue", "manager.value is not a user of this source"
            ) from e


async def _lifecycle(
    conn: AsyncConnection, src: Source, r: Resource, *, active: bool, now: datetime
) -> None:
    """Deprovision / reprovision through the signals path (AUTH-11)."""
    if r.principal_id is None:
        return
    await ingest(
        conn,
        CanonicalSignal(
            tenant_id=src.tenant_id,
            subject_type="PRINCIPAL",
            subject_ref=r.principal_id,
            source_id=f"scim:{src.source_id}",
            source_event_id=f"{r.resource_id}:v{r.version}:{'on' if active else 'off'}",
            source_event_type="scim:active",
            event_type="account.enabled" if active else "account.disabled",
            effective_at=now,
            observed_at=now,
            confidence="AUTHORITATIVE",
        ),
        now=now,
    )


async def create_user(
    conn: AsyncConnection, src: Source, payload: dict[str, Any], *, home_cell: str, now: datetime
) -> Resource:
    user = parse_user(payload, Context.RESOURCE_CREATION_REQUEST)
    if not user.user_name:
        raise ScimError(400, "invalidValue", "userName is required")
    _fit(userName=user.user_name, externalId=user.external_id, displayName=user.display_name)
    principal_id = str(uuid.uuid4())
    await create_principal(
        conn,
        Principal(
            principal_id, src.tenant_id, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, home_cell
        ),
    )
    r = Resource(
        resource_id=str(uuid.uuid4()),
        tenant_id=src.tenant_id,
        source_id=src.source_id,
        resource_type="User",
        external_id=user.external_id,
        principal_id=principal_id,
        user_name=user.user_name,
        display_name=user.display_name,
        active=user.active is not False,
        attributes=_attrs(user),
        version=1,
        created_at=now,
        updated_at=now,
    )
    await _check_manager(conn, src, r.attributes)
    await store.insert(conn, r)
    if not r.active:
        await _lifecycle(conn, src, r, active=False, now=now)
    return r


def _check_version(r: Resource, if_match: str | None) -> None:
    """RFC 7644 §3.14: `If-Match` carries one or more entity tags (weak or strong) or `*`."""
    if if_match is None:
        return
    tags = [t.strip().removeprefix("W/") for t in if_match.split(",")]
    if "*" not in tags and f'"{r.version}"' not in tags:
        raise ScimError(412, None, "version mismatch")


async def _apply_user(
    conn: AsyncConnection, src: Source, old: Resource, user: ScimUser, now: datetime
) -> Resource:
    # `user` is the complete new state (a PUT body, or the patched copy of the stored resource), so
    # an omitted or removed externalId / displayName / name / emails is cleared, not kept.
    _fit(userName=user.user_name, externalId=user.external_id, displayName=user.display_name)
    new = replace(
        old,
        external_id=user.external_id,
        user_name=user.user_name or old.user_name,
        display_name=user.display_name,
        active=old.active if user.active is None else bool(user.active),
        attributes=_attrs(user),
        version=old.version + 1,
        updated_at=now,
    )
    await _check_manager(conn, src, new.attributes)
    await store.update(conn, old, new)
    if new.active != old.active:
        await _lifecycle(conn, src, new, active=new.active, now=now)
    return new


async def replace_user(
    conn: AsyncConnection,
    src: Source,
    resource_id: str,
    payload: dict[str, Any],
    *,
    if_match: str | None,
    now: datetime,
) -> Resource:
    old = await store.load(conn, src.source_id, "User", resource_id, lock=True)
    _check_version(old, if_match)
    user = parse_user(payload, Context.RESOURCE_REPLACEMENT_REQUEST)
    return await _apply_user(conn, src, old, user, now)


async def patch_user(
    conn: AsyncConnection,
    src: Source,
    resource_id: str,
    payload: dict[str, Any],
    *,
    if_match: str | None,
    now: datetime,
) -> Resource:
    old = await store.load(conn, src.source_id, "User", resource_id, lock=True)
    _check_version(old, if_match)
    try:
        patch = PatchOp[ScimUser].model_validate(payload, scim_ctx=Context.RESOURCE_PATCH_REQUEST)
    except ValidationError as e:
        raise _invalid(e) from e
    current: ScimUser = ScimUser.model_validate(
        to_scim_user(old, "", include_meta=False), scim_ctx=Context.RESOURCE_REPLACEMENT_REQUEST
    )
    try:
        patch.patch(current)
    except (SCIMException, ValueError) as e:
        raise ScimError(400, getattr(e, "scim_type", "invalidValue"), str(e)[:200]) from e
    return await _apply_user(conn, src, old, current, now)


async def delete_resource(
    conn: AsyncConnection,
    src: Source,
    resource_type: str,
    resource_id: str,
    *,
    now: datetime,
    if_match: str | None = None,
) -> None:
    r = await store.load(conn, src.source_id, resource_type, resource_id, lock=True)
    _check_version(r, if_match)
    await store.mark_deleted(conn, r, now)
    if resource_type == "User" and r.active:
        await _lifecycle(conn, src, replace(r, version=r.version + 1), active=False, now=now)


def to_scim_user(
    r: Resource,
    base: str,
    *,
    include_meta: bool = True,
    groups: list[tuple[str, str | None]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": r.resource_id,
        "userName": r.user_name,
        "active": r.active,
        **{k: v for k, v in r.attributes.items() if k in USER_ATTRIBUTES},
    }
    manager = (out.get(ENTERPRISE) or {}).get("manager")
    if manager and manager.get("value"):  # echo the manager as a reference to that user
        out[ENTERPRISE] = {
            **out[ENTERPRISE],
            "manager": {**manager, "$ref": f"{base}/scim/v2/Users/{manager['value']}"},
        }
    if ENTERPRISE in out:
        out["schemas"].append(ENTERPRISE)
    if r.external_id is not None:
        out["externalId"] = r.external_id
    if r.display_name is not None:
        out["displayName"] = r.display_name
    if groups:  # read-only: the groups this user is a member of, from the membership facts
        out["groups"] = [
            {
                "value": gid,
                "$ref": f"{base}/scim/v2/Groups/{gid}",
                **({"display": name} if name else {}),
                "type": "direct",
            }
            for gid, name in groups
        ]
    if include_meta:
        out["meta"] = {
            "resourceType": "User",
            "created": r.created_at.isoformat(),
            "lastModified": r.updated_at.isoformat(),
            "version": f'W/"{r.version}"',
            "location": f"{base}/scim/v2/Users/{r.resource_id}",
        }
    return out


async def render_user(conn: AsyncConnection, r: Resource, base: str) -> dict[str, Any]:
    """The SCIM User with its read-only `groups`."""
    return to_scim_user(r, base, groups=await store.groups_of(conn, r.resource_id))


# ------------------------------------------------------------------------------------------ groups


def parse_group(payload: dict[str, Any], ctx: Context) -> Group:
    try:
        return Group.model_validate(payload, scim_ctx=ctx)
    except ValidationError as e:
        raise _invalid(e) from e


async def _member_ids(conn: AsyncConnection, src: Source, values: list[str]) -> list[str]:
    out = []
    for v in values:
        await store.load(conn, src.source_id, "User", v)  # 404 for a member of another source
        out.append(v)
    return out


async def create_group(
    conn: AsyncConnection, src: Source, payload: dict[str, Any], *, now: datetime
) -> Resource:
    g = parse_group(payload, Context.RESOURCE_CREATION_REQUEST)
    if not g.display_name:
        raise ScimError(400, "invalidValue", "displayName is required")
    _fit(displayName=g.display_name, externalId=g.external_id)
    r = Resource(
        resource_id=str(uuid.uuid4()),
        tenant_id=src.tenant_id,
        source_id=src.source_id,
        resource_type="Group",
        external_id=g.external_id,
        principal_id=None,
        user_name=None,
        display_name=g.display_name,
        active=True,
        attributes={},
        version=1,
        created_at=now,
        updated_at=now,
    )
    await store.insert(conn, r)
    members = [m.value for m in (g.members or []) if m.value]
    await store.set_members(
        conn,
        tenant_id=src.tenant_id,
        group_id=r.resource_id,
        add=await _member_ids(conn, src, members),
        remove=[],
    )
    return r


def _values(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(v["value"] if isinstance(v, dict) else getattr(v, "value", v)) for v in items]


def _operations(patch: PatchOp[Group]) -> list[tuple[str, str, Any]]:
    """(op, path, value); a pathless add/replace carrying an object is split per attribute."""
    out: list[tuple[str, str, Any]] = []
    for op in patch.operations or []:
        kind = str(getattr(op.op, "value", op.op)).lower()
        path = (op.path or "").strip()
        value = op.value
        if not path and kind in ("add", "replace") and isinstance(value, dict):
            out += [(kind, k, v) for k, v in value.items()]
        else:
            out.append((kind, path, value))
    return out


async def patch_group(
    conn: AsyncConnection,
    src: Source,
    resource_id: str,
    payload: dict[str, Any],
    *,
    if_match: str | None = None,
    now: datetime,
) -> Resource:
    old = await store.load(conn, src.source_id, "Group", resource_id, lock=True)
    _check_version(old, if_match)
    try:
        patch = PatchOp[Group].model_validate(payload, scim_ctx=Context.RESOURCE_PATCH_REQUEST)
    except ValidationError as e:
        raise _invalid(e) from e
    add: list[str] = []
    remove: list[str] = []
    display_name = old.display_name
    external_id = old.external_id
    for kind, path, value in _operations(patch):
        if path == "members" and kind in ("add", "replace"):
            add += _values(value)
            if kind == "replace":
                remove += await store.members(conn, resource_id)
        elif path.startswith("members[value eq ") and kind == "remove":
            remove.append(path.split('"')[1])
        elif path == "members" and kind == "remove":
            remove += _values(value) if value else await store.members(conn, resource_id)
        elif path == "displayName" and kind in ("add", "replace"):
            display_name = str(value) if value else None
        elif path == "externalId" and kind in ("add", "replace", "remove"):
            external_id = str(value) if value and kind != "remove" else None
        else:
            raise ScimError(400, "invalidPath", f"unsupported group operation {kind} {path}")
    if not display_name:
        raise ScimError(400, "invalidValue", "displayName is required")
    _fit(displayName=display_name, externalId=external_id)
    await store.set_members(
        conn,
        tenant_id=src.tenant_id,
        group_id=resource_id,
        add=await _member_ids(conn, src, add),
        remove=[m for m in remove if m not in add],
    )
    new = replace(
        old,
        display_name=display_name,
        external_id=external_id,
        version=old.version + 1,
        updated_at=now,
    )
    await store.update(conn, old, new)
    return new


async def replace_group(
    conn: AsyncConnection,
    src: Source,
    resource_id: str,
    payload: dict[str, Any],
    *,
    if_match: str | None,
    now: datetime,
) -> Resource:
    old = await store.load(conn, src.source_id, "Group", resource_id, lock=True)
    _check_version(old, if_match)
    g = parse_group(payload, Context.RESOURCE_REPLACEMENT_REQUEST)
    if not g.display_name:
        raise ScimError(400, "invalidValue", "displayName is required")
    _fit(displayName=g.display_name, externalId=g.external_id)
    wanted = [m.value for m in (g.members or []) if m.value]
    current = await store.members(conn, resource_id)
    await store.set_members(
        conn,
        tenant_id=src.tenant_id,
        group_id=resource_id,
        add=await _member_ids(conn, src, wanted),
        remove=[m for m in current if m not in wanted],
    )
    new = replace(
        old,
        display_name=g.display_name,
        external_id=g.external_id,
        version=old.version + 1,
        updated_at=now,
    )
    await store.update(conn, old, new)
    return new


async def to_scim_group(conn: AsyncConnection, r: Resource, base: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": r.resource_id,
        "displayName": r.display_name,
        **({"externalId": r.external_id} if r.external_id else {}),
        "members": [
            {
                "value": m,
                "$ref": f"{base}/scim/v2/Users/{m}",
                **({"display": name} if name else {}),
                "type": "User",
            }
            for m, name in await store.members_detail(conn, r.resource_id)
        ],
        "meta": {
            "resourceType": "Group",
            "created": r.created_at.isoformat(),
            "lastModified": r.updated_at.isoformat(),
            "version": f'W/"{r.version}"',
            "location": f"{base}/scim/v2/Groups/{r.resource_id}",
        },
    }
