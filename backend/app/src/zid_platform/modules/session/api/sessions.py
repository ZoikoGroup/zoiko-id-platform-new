"""Identity sessions of the authenticated principal (ZID-AUTH-001 §5, §16, §22).

    GET  /sessions                   the caller's live sessions (ids, assurance, times only)
    POST /sessions/{id}:revoke       local logout of one of the caller's sessions
    POST /sessions:revoke-all        global logout: every session (and so every refresh family)
    POST /admin/principals/{id}/sessions:revoke   protected admin bulk revoke (session.bulk_revoke)

Revoking a session ends the refresh families bound to it at their next redemption, and access
tokens naming it fail at once (the resource server checks the session on every request).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.session.domain.session import SessionStatus
from zid_platform.modules.session.infrastructure import session_store
from zid_platform.protected import protected_action

router = APIRouter(tags=["sessions"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/sessions")
async def list_sessions(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await session_store.list_sessions(
            conn, caller.ctx.principal_id or "", datetime.now(UTC)
        )
    return {
        "items": [
            {
                "session_id": s.session_id,
                "status": s.status.value,
                "aal": s.aal,
                "authentication_context": s.authentication_context,
                "auth_time": s.auth_time.isoformat(),
                "expires_at": s.expires_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in items
        ]
    }


@router.post("/sessions/{session_id}:revoke")
async def revoke_session(
    session_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await session_store.load_session(conn, str(session_id))
        if s is None or s.principal_id != caller.ctx.principal_id:
            raise http_error("SESSION_NOT_FOUND")
        if s.status in (SessionStatus.ACTIVE, SessionStatus.REAUTH_REQUIRED):
            await session_store.set_status(
                conn, s.session_id, SessionStatus.REVOKED, reason="user_logout"
            )
    return {"session_id": str(session_id), "status": "REVOKED"}


@router.post("/sessions:revoke-all")
async def revoke_all(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        n = await session_store.revoke_principal_sessions(
            conn, caller.ctx.principal_id or "", reason="global_logout"
        )
    return {"revoked": n}


@router.post("/admin/principals/{principal_id}/sessions:revoke")
async def admin_revoke_all(
    principal_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """§24 protected administrator action: bulk session revoke for one principal."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="session.bulk_revoke", resource=f"principal:{principal_id}"
        )
        n = await session_store.revoke_principal_sessions(
            conn, str(principal_id), reason=f"admin:{caller.ctx.principal_id}"
        )
    return {"principal_id": str(principal_id), "revoked": n}
