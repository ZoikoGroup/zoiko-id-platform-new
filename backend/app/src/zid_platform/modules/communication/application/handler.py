"""The consumer-side glue: one platform event in, zero or more emails out (Communication Estate §03).

Runs in the event-consumer process beside the alerts and webhook routers. It reads the recipients and merge
values inside the tenant's own transaction, then sends each email outside any transaction. It never raises
for a delivery problem: a failed send is a row in the message log, not a crashed consumer (a raised error
would stop the whole consumer and block every other topic behind it).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine
from zid_context import RequestContext, new_id
from zid_db import tenant_transaction
from zid_outbox import CloudEvent

from zid_platform.modules.communication.application import dispatch
from zid_platform.modules.communication.application.config import EmailConfig
from zid_platform.modules.communication.application.service import send_email
from zid_platform.modules.communication.infrastructure.smtp import EmailSender

log = logging.getLogger("zid.communication")


def email_topics(env: str, region: str) -> list[str]:
    domains = sorted({name.split(".")[0] for name in dispatch.EVENT_ROUTES})
    return [
        f"zid.{env}.{region}.{d}.{lane}"
        for d in domains
        for lane in ("priority", "domain", "integration")
    ]


def email_handler(engine: AsyncEngine, cell_id: str, cfg: EmailConfig, sender: EmailSender):  # type: ignore[no-untyped-def]
    async def handle(event: CloudEvent) -> None:
        name = dispatch.event_name(event.type)
        if name not in dispatch.EVENT_ROUTES or not event.tenantid or not cfg.enabled:
            return
        ctx = RequestContext(
            new_id("req"), event.correlationid, "communication", cell_id, tenant_id=event.tenantid
        )
        try:
            async with tenant_transaction(engine, ctx) as conn:
                jobs = await dispatch.plan(
                    conn,
                    event_type=event.type,
                    event_id=event.id,
                    event_time=event.time,
                    tenant_id=event.tenantid,
                    data=event.data,
                )
        except Exception:  # planning is read-only; a broken lookup must not stop the consumer
            log.exception("email planning failed for %s", name)
            return
        for job in jobs:
            await send_email(
                engine,
                cfg,
                sender,
                cell_id=cell_id,
                template_id=job.template_id,
                event_id=event.id,
                event_type=event.type,
                tenant_id=event.tenantid,
                recipient=job.recipient,
                merge=job.merge,
                correlation_id=event.correlationid,
            )

    return handle
