"""Tenant settings API (ZID-UX-001 §19, §20, §28; CR-022).

GET  /v1/settings                      every area's current revision and value      (configuration.read)
GET  /v1/settings/cryptography         key mode, key health and algorithm policy; no key material (configuration.read)
GET  /v1/settings/{area}/history       the area's revisions, newest first          (configuration.read)
PUT  /v1/settings/{area}               replace the area's value; names the revision it was based on (configuration.manage)

Areas: AUTHENTICATION (session limits, shortened only), NOTIFICATIONS (mute non-mandatory alert kinds),
BRANDING (presentation inside anti-spoofing rules). A write based on a stale revision is refused with a
visible 409 CONFIGURATION_CONFLICT and the current revision, never silently applied (UX-06).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_crypto.policy import default_registry
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.configuration.application import settings as service
from zid_platform.modules.configuration.domain.settings import AREAS, CUSTOM_THEME_FIELDS, SettingsError
from zid_platform.modules.configuration.infrastructure import snapshot_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["settings"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Area = Literal["AUTHENTICATION", "NOTIFICATIONS", "BRANDING"]


def _fail(e: SettingsError) -> ZidError:
    status = {
        "CONFIGURATION_CONFLICT": 409,
        "NOTIFICATION_MANDATORY": 422,
        "BRANDING_SPOOFING": 422,
        "VALIDATION_FAILED": 422,
        "SETTINGS_AREA_UNKNOWN": 404,
    }.get(e.code, 409)
    return ZidError(e.code, str(e), http_status=status)


class SettingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(
        ge=0, description="The revision this edit was based on (0 = never set)."
    )
    value: dict[str, Any]


@router.get("/v1/settings")
async def all_settings(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="configuration.read", resource="settings:*")
        return {"areas": await service.get_all(conn)}


@router.get("/v1/settings/cryptography")
async def cryptography(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What protects this tenant's data, with no key material: who holds the keys, how healthy they are, and
    which algorithms each purpose may use."""
    from zid_platform.modules.tenant.ports import load_residency

    registry = default_registry()
    purposes = []
    for purpose in registry.purposes():
        purposes.append(
            {
                "purpose": purpose.value,
                "profile": registry.profile_for(purpose),
                "algorithms": list(registry.algorithms(purpose)),
            }
        )
    rings = getattr(request.app.state, "keyrings", None)
    published = (
        {
            name: [k.get("kid") for k in getattr(rings, name).jwks()["keys"]]
            for name in ("token", "set", "federation", "credential", "verifier")
        }
        if rings is not None
        else {}
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="configuration.read", resource="settings:cryptography"
        )
        residency = await load_residency(conn, caller.ctx.tenant_id or "")
    return {
        "key_mode": "PLATFORM_MANAGED",
        "customer_managed_keys": {
            "byok": {"available": False, "state": "NOT_ENABLED"},
            "hyok": {"available": False, "state": "NOT_ENABLED"},
        },
        "key_provider": getattr(request.app.state, "key_provider", "unknown"),
        "residency_key_profile": getattr(residency, "key_profile", None),
        "policy_registry_version": registry.version,
        "purposes": purposes,
        "published_signing_key_ids": published,
        "note": "Raw keys are never shown. Key rotation and ceremonies are run by the platform.",
    }


@router.get("/v1/settings/notification-kinds")
async def notification_kinds(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The alert kinds a tenant sees, and which of them are mandatory (cannot be muted)."""
    from zid_platform.modules.notification.ports import MANDATORY_KINDS, TEMPLATES

    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="configuration.read", resource="settings:NOTIFICATIONS"
        )
    return {
        "items": [
            {
                "kind": kind,
                "title": t.title,
                "severity": t.severity.value,
                "mandatory": kind in MANDATORY_KINDS,
            }
            for kind, t in sorted(TEMPLATES.items(), key=lambda kv: (kv[1].severity.value, kv[0]))
        ]
    }


@router.get("/v1/settings/{area}/history")
async def area_history(area: Area, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="configuration.read", resource=f"settings:{area}"
        )
        return {"area": area, "items": await store.history(conn, area)}


@router.put("/v1/settings/{area}")
async def put_setting(
    area: Area, body: SettingBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    assert area in AREAS
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action="configuration.manage",
                resource=f"settings:{area}",
                change=area,
            )
            if area == "BRANDING" and any(body.value.get(f) for f in CUSTOM_THEME_FIELDS):
                await require_feature(
                    conn,
                    "standard.custom-domain-hosted-brand-theme",
                    enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
                )
            return await service.update(
                conn,
                caller.ctx.tenant_id or "",
                area,
                expected_revision=body.expected_revision,
                value=body.value,
                actor=caller.ctx.principal_id or "",
            )
    except SettingsError as e:
        if e.code == "CONFIGURATION_CONFLICT":
            # the visible conflict carries the current revision so the editor can reload and merge
            async with tenant_transaction(request_engine(request), caller.ctx) as conn:
                current = await store.current(conn, area)
            raise ZidError(
                e.code,
                f"This setting changed since you opened it (now revision {current['revision']}). Reload and review before saving.",
                http_status=409,
            ) from e
        raise _fail(e) from e
