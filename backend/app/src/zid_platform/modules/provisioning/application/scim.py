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
from typing import Any

from pydantic import ValidationError
from scim2_models import Context, Group, PatchOp, SCIMException, User
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

ScimUser = User[Any]  # core User; enterprise extensions are not accepted
USER_ATTRIBUTES = ("name", "emails")  # the minimum SCIM must echo back; nothing else is kept


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


def _attrs(user: ScimUser) -> dict[str, Any]:
    dumped = user.model_dump(scim_ctx=Context.RESOURCE_CREATION_REQUEST)
    return {k: dumped[k] for k in USER_ATTRIBUTES if dumped.get(k) is not None}


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
    await store.insert(conn, r)
    if not r.active:
        await _lifecycle(conn, src, r, active=False, now=now)
    return r


def _check_version(r: Resource, if_match: str | None) -> None:
    if if_match is not None and if_match.strip() not in (f'W/"{r.version}"', "*"):
        raise ScimError(412, None, "version mismatch")


async def _apply_user(
    conn: AsyncConnection, src: Source, old: Resource, user: ScimUser, now: datetime
) -> Resource:
    new = replace(
        old,
        external_id=user.external_id if user.external_id is not None else old.external_id,
        user_name=user.user_name or old.user_name,
        display_name=user.display_name if user.display_name is not None else old.display_name,
        active=old.active if user.active is None else bool(user.active),
        attributes={**old.attributes, **_attrs(user)},
        version=old.version + 1,
        updated_at=now,
    )
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
    conn: AsyncConnection, src: Source, resource_type: str, resource_id: str, *, now: datetime
) -> None:
    r = await store.load(conn, src.source_id, resource_type, resource_id, lock=True)
    await store.mark_deleted(conn, r, now)
    if resource_type == "User" and r.active:
        await _lifecycle(conn, src, replace(r, version=r.version + 1), active=False, now=now)


def to_scim_user(r: Resource, base: str, *, include_meta: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": r.resource_id,
        "userName": r.user_name,
        "active": r.active,
        **{k: v for k, v in r.attributes.items() if k in USER_ATTRIBUTES},
    }
    if r.external_id is not None:
        out["externalId"] = r.external_id
    if r.display_name is not None:
        out["displayName"] = r.display_name
    if include_meta:
        out["meta"] = {
            "resourceType": "User",
            "created": r.created_at.isoformat(),
            "lastModified": r.updated_at.isoformat(),
            "version": f'W/"{r.version}"',
            "location": f"{base}/scim/v2/Users/{r.resource_id}",
        }
    return out


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


async def patch_group(
    conn: AsyncConnection, src: Source, resource_id: str, payload: dict[str, Any], *, now: datetime
) -> Resource:
    old = await store.load(conn, src.source_id, "Group", resource_id, lock=True)
    try:
        patch = PatchOp[Group].model_validate(payload, scim_ctx=Context.RESOURCE_PATCH_REQUEST)
    except ValidationError as e:
        raise _invalid(e) from e
    add: list[str] = []
    remove: list[str] = []
    display_name = old.display_name
    for op in patch.operations or []:
        path = (op.path or "").strip()
        if path == "members" and op.op in ("add", "replace"):
            values = op.value if isinstance(op.value, list) else [op.value]
            add += [
                str(v["value"] if isinstance(v, dict) else getattr(v, "value", v)) for v in values
            ]
            if op.op == "replace":
                remove += [m for m in await store.members(conn, resource_id)]
        elif path.startswith("members[value eq ") and op.op == "remove":
            remove.append(path.split('"')[1])
        elif path == "members" and op.op == "remove":
            remove += await store.members(conn, resource_id)
        elif path == "displayName" and op.op in ("add", "replace"):
            display_name = str(op.value)
        else:
            raise ScimError(400, "invalidPath", f"unsupported group operation {op.op} {path}")
    await store.set_members(
        conn,
        tenant_id=src.tenant_id,
        group_id=resource_id,
        add=await _member_ids(conn, src, add),
        remove=[m for m in remove if m not in add],
    )
    new = replace(old, display_name=display_name, version=old.version + 1, updated_at=now)
    await store.update(conn, old, new)
    return new


async def to_scim_group(conn: AsyncConnection, r: Resource, base: str) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": r.resource_id,
        "displayName": r.display_name,
        **({"externalId": r.external_id} if r.external_id else {}),
        "members": [
            {"value": m, "$ref": f"{base}/scim/v2/Users/{m}", "type": "User"}
            for m in await store.members(conn, r.resource_id)
        ],
        "meta": {
            "resourceType": "Group",
            "created": r.created_at.isoformat(),
            "lastModified": r.updated_at.isoformat(),
            "version": f'W/"{r.version}"',
            "location": f"{base}/scim/v2/Groups/{r.resource_id}",
        },
    }
