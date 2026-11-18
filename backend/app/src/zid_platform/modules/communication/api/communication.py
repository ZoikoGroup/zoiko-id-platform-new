"""Email contacts and the public unsubscribe endpoint (Communication Estate §03, §04).

Tenant (communication.read, communication.contact.manage)
    GET    /v1/tenant/contacts           the organization's role contacts, addresses masked
    PUT    /v1/tenant/contacts           add a billing / security / technical / owner contact
    DELETE /v1/tenant/contacts/{id}      retire a contact
    PUT    /v1/account/contact           ask to change where the caller's own security notices go (confirmed by the new address)
    POST   /v1/account/contact:confirm   the single-use link from that email
Public
    GET    /email/unsubscribe            a confirmation page (a GET never changes anything)
    POST   /email/unsubscribe            RFC 8058 one-click: records the unsubscribe

An address is accepted from an authenticated caller and stored encrypted in the vault; no endpoint ever
returns one in full. The unsubscribe link carries a keyed signature over the recipient hash and list, so it
can be neither forged nor aimed at someone else, and it answers the same whether or not the address was known.
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.communication.application import contact_change, contacts
from zid_platform.modules.communication.application.contacts import ContactError
from zid_platform.modules.communication.application.service import send_email, verify_unsubscribe
from zid_platform.modules.communication.domain.policy import Suppression
from zid_platform.modules.communication.infrastructure import store
from zid_platform.protected import protected_action

log = logging.getLogger("zid.communication")
router = APIRouter(tags=["communication"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


class ContactBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["OWNER", "BILLING", "SECURITY", "TECHNICAL"]
    address: str = Field(min_length=3, max_length=254)


class SelfContactBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)


def _invalid(e: ContactError) -> ZidError:
    return ZidError("VALIDATION_FAILED", "That is not a valid email address.", http_status=422)


@router.get("/v1/tenant/contacts")
async def list_contacts(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="communication.read", resource=f"tenant:{tenant}"
        )
        return {"items": await contacts.list_masked(conn, tenant)}


@router.put("/v1/tenant/contacts", status_code=201)
async def add_contact(body: ContactBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="communication.contact.manage", resource=f"tenant:{tenant}"
        )
        try:
            contact_id = await contacts.add_role_contact(
                conn, tenant, body.purpose, body.address, actor=caller.ctx.principal_id
            )
        except ContactError as e:
            raise _invalid(e) from e
    return {
        "contact_id": contact_id,
        "purpose": body.purpose,
        "address": contacts.mask(contacts.normalize_address(body.address)),
    }


@router.delete("/v1/tenant/contacts/{contact_id}", status_code=204)
async def retire_contact(contact_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="communication.contact.manage", resource=f"tenant:{tenant}"
        )
        if not await store.retire_contact(conn, tenant, str(contact_id)):
            raise ZidError("CONTACT_NOT_FOUND", "No such contact.", http_status=404)
    return Response(status_code=204)


@router.put("/v1/account/contact", status_code=202)
async def set_own_contact(
    body: SelfContactBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A change waits for the new address to confirm it (ZID-EM-017 with a single-use link); the old address is told (ZID-EM-018). CR-114."""
    tenant, me = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="communication.contact.manage", resource=f"principal:{me}"
        )
        try:
            req = await contact_change.request(conn, tenant, me, body.address, now=now)
        except ContactError as e:
            raise _invalid(e) from e
    await _mail(request, tenant, me, "ZID-EM-017", "identity.email.change_new_verification", req.change_id, req.new_address,
                {"single_use_email_change_url": req.token, "expires_at_local": req.expires_at})  # fmt: skip
    if req.old_address:
        await _mail(request, tenant, me, "ZID-EM-018", "identity.email.change_requested_old", req.change_id, req.old_address,
                    {"event_at_local": now})  # fmt: skip
    return {
        "change_id": req.change_id,
        "status": "PENDING_CONFIRMATION",
        "address": contacts.mask(req.new_address),
        "expires_at": req.expires_at.isoformat(),
    }


class ConfirmChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=75, max_length=75)


@router.post("/v1/account/contact:confirm")
async def confirm_own_contact(
    body: ConfirmChangeBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The single-use link from ZID-EM-017, opened by the signed-in person. Then ZID-EM-019 goes to both addresses."""
    tenant, me = caller.ctx.tenant_id or "", caller.ctx.principal_id or ""
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            new, old = await contact_change.confirm(conn, tenant, me, body.token, now=now)
        except contact_change.ChangeError as e:
            raise ZidError(
                e.code, "The link is not valid. Ask for a new one.", http_status=422
            ) from e
    for address in (new, old):
        if address:
            await _mail(
                request,
                tenant,
                me,
                "ZID-EM-019",
                "identity.email.changed",
                body.token[:32],
                address,
                {"event_at_local": now},
            )
    return {"status": "APPLIED", "address": contacts.mask(new)}


async def _mail(
    request: Request,
    tenant: str,
    principal: str,
    template: str,
    event: str,
    ref: str,
    address: str,
    merge: dict[str, Any],
) -> None:
    """After the transaction. A delivery problem is logged without the address and never changes the answer."""
    cfg = getattr(request.app.state, "email_config", None)
    sender = getattr(request.app.state, "email_sender", None)
    if cfg is None or sender is None:
        log.warning("email change message not sent: email is not configured")
        return
    try:
        recipient = contacts.Recipient(address, await contacts.address_hash(address))
        await send_email(
            request_engine(request), cfg, sender, cell_id=str(getattr(request.app.state, "home_cell", "")), template_id=template,
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"zid:email-change:{ref}:{template}:{recipient.address_hash}")),
            event_type=event, tenant_id=tenant, recipient=recipient, merge=merge,
        )  # fmt: skip
    except Exception:  # noqa: BLE001 - delivery never changes the answer
        log.warning(
            "email change message %s failed for principal %s",
            template,
            principal[:8],
            exc_info=False,
        )


# ------------------------------------------------------------------------------- unsubscribe (public)

_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
    'content="width=device-width, initial-scale=1"><title>Unsubscribe</title></head>'
    '<body style="font-family:Arial,sans-serif;max-width:480px;margin:48px auto;padding:0 16px">'
    "<h1>Unsubscribe from Zoiko iD updates</h1>"
    "<p>This stops optional product updates. Account, security, privacy and billing notices you need "
    "will still be sent.</p>"
    '<form method="post" action="{action}"><button type="submit" style="padding:10px 18px">Unsubscribe</button></form>'
    "</body></html>"
)


@router.get("/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(
    r: Annotated[str, Query(min_length=20, max_length=128)],
    l: Annotated[str, Query(min_length=1, max_length=80)],
    s: Annotated[str, Query(min_length=20, max_length=100)],
) -> HTMLResponse:
    if not verify_unsubscribe(r, l, s):
        raise ZidError("LINK_INVALID", "This link is not valid.", http_status=400)
    from urllib.parse import quote

    action = f"/email/unsubscribe?r={quote(r, safe='')}&l={quote(l, safe='')}&s={quote(s, safe='')}"
    return HTMLResponse(
        _PAGE.format(action=html.escape(action, quote=True)), headers={"Cache-Control": "no-store"}
    )


@router.post("/email/unsubscribe")
async def unsubscribe(
    request: Request,
    r: Annotated[str, Query(min_length=20, max_length=128)],
    l: Annotated[str, Query(min_length=1, max_length=80)],
    s: Annotated[str, Query(min_length=20, max_length=100)],
) -> dict[str, str]:
    if not verify_unsubscribe(r, l, s):
        raise ZidError("LINK_INVALID", "This link is not valid.", http_status=400)
    async with request_engine(request).begin() as conn:
        await store.add_suppression(
            conn, r, Suppression.UNSUBSCRIBED, source="unsubscribe_link", list_id=l
        )
        await store.record_consent(conn, r, l, "WITHDRAWN", "unsubscribed by the recipient")
    return {"status": "UNSUBSCRIBED"}
