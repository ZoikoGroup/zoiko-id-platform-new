"""A tenant's private issuer trust never authorises another tenant's verification (ZID-VC-001 VC-26).

Issuer trust is tenant data: an entry one tenant pinned is invisible to every other tenant, so the same issuer is
untrusted there until that tenant pins it itself.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.credential.infrastructure import credential_store as store

ISSUER = "https://issuer.private.example"


def uid() -> str:
    return str(uuid.uuid4())


def test_private_trust_entry_is_invisible_to_another_tenant(rt_url: str) -> None:
    """VC-26: tenant A pins an issuer; tenant B finds no trust for it, by issuer or by credential type."""
    a, b = uid(), uid()

    async def run() -> tuple[object, object, list[object], list[object]]:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(a)) as c:
                await c.execute(
                    text(
                        "INSERT INTO cred.issuer_trust (trust_id, tenant_id, issuer_identifier, credential_types,"
                        " jurisdictions, trust_source, jwks, valid_to) VALUES (:t, :a, :i, ARRAY['pid'],"
                        " ARRAY['GB'], 'PINNED', '{\"keys\": []}'::jsonb, :v)"
                    ),
                    {"t": uid(), "a": a, "i": ISSUER, "v": datetime.now(UTC) + timedelta(days=30)},
                )
            async with tenant_transaction(eng, ctx(a)) as c:
                own = await store.load_trust(c, ISSUER)
                own_types = await store.trust_for_type(c, "pid")
            async with tenant_transaction(eng, ctx(b)) as c:
                other = await store.load_trust(c, ISSUER)
                other_types = await store.trust_for_type(c, "pid")
            return own, other, own_types, other_types

    own, other, own_types, other_types = asyncio.run(run())
    assert own is not None and len(own_types) == 1
    assert other is None
    assert other_types == []
