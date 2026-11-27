"""Photographs of a partner's customer and the single-approver table rules, on PostgreSQL (migrations verify_api_0019 and verify_api_0020).

A photograph is kept only when the deployment chose a number of days; it is listed without its bytes, opened only with a typed reason, and every opening is
recorded before the image leaves. The tables are tenant-scoped under row-level security, and the access record cannot be changed or deleted.
The "a different person decides" rules in the tables yield only when the transaction carries the single-approver flag.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import media_service as media
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import media_store as store
from zid_platform.modules.verify_api.infrastructure import single_approver

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 64
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


class FakeVault:
    """Stands in for the evidence module's review-photo port: the vault itself is proved elsewhere (the IDV media tests)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.gone: set[str] = set()

    async def keep(
        self,
        conn: Any,
        tenant_id: str,
        *,
        content: bytes,
        subject_principal_id: str,
        expires_at: datetime,
    ) -> tuple[str, str]:
        object_id = "obj_" + uuid.uuid4().hex
        self.objects[object_id] = content
        return object_id, "sha-256:" + "0" * 64

    async def read(self, conn: Any, tenant_id: str, object_id: str) -> bytes:
        if object_id in self.gone:
            raise LookupError("erased")
        return self.objects[object_id]


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> FakeVault:
    v = FakeVault()
    monkeypatch.setattr(media, "review_photo", v)
    monkeypatch.setattr(
        media.partner_capture, "applicant_for", lambda session_id: str(uuid.uuid4())
    )
    return v


@pytest.fixture
def keep_three_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZID_VERIFY_KEEP_PHOTOS_DAYS", "3")


class Company:
    """A company with one application and one session, planted with the owner role (it bypasses row-level security)."""

    def __init__(self, world: World, owner_url: str, tenant: str | None = None) -> None:
        self.tenant = tenant or world.tenant
        headers = world.server(tenant=self.tenant)
        app_id = world.http.get("/v1/verify-api/whoami", headers=headers).json()["application_id"]
        self.session_id = "vfs_" + uuid.uuid4().hex

        async def plant() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO verify.api_session (session_id, tenant_id, application_id, environment, customer_reference, workflow_id, workflow_version, country, region, purpose_code, locale, status, expires_at) "
                        "VALUES (:s, :t, :a, 'SANDBOX', 'media-ref', 'default', '2026-11-sandbox', 'GB', 'uk', 'customer_onboarding', 'en', 'ready', :e)"
                    ),
                    {
                        "s": self.session_id,
                        "t": uuid.UUID(self.tenant),
                        "a": uuid.UUID(app_id),
                        "e": datetime.now(UTC) + timedelta(days=1),
                    },
                )

        asyncio.run(plant())


@pytest.fixture
def world(rt_url: str) -> Any:
    w = World(rt_url)
    try:
        yield w
    finally:
        w.close()


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as conn:
            return await fn(conn)

    return asyncio.run(go())


def keep(
    rt_url: str, co: Company, document: bytes = JPEG, selfie: bytes = PNG, now: datetime = NOW
) -> int:
    return int(
        run(
            rt_url,
            co.tenant,
            lambda c: media.keep_capture(
                c,
                tenant_id=co.tenant,
                session_id=co.session_id,
                document=document,
                selfie=selfie,
                captured_at=now,
                now=now,
            ),
        )
    )


def test_nothing_is_kept_unless_the_deployment_chose_a_number_of_days(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ZID_VERIFY_KEEP_PHOTOS_DAYS", raising=False)
    co = Company(world, owner_url)
    assert keep(rt_url, co) == 0 and vault.objects == {}
    listing = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))
    assert listing["keeping_days"] == 0 and listing["items"] == [] and listing["opened"] == []


def test_a_kept_photograph_is_listed_without_its_bytes_and_each_new_attempt_is_numbered(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    co = Company(world, owner_url)
    assert keep(rt_url, co) == 2
    assert keep(rt_url, co) == 2  # a second capture of the same session
    listing = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))
    assert listing["keeping_days"] == 3
    assert sorted((i["attempt"], i["kind"]) for i in listing["items"]) == [
        (1, "DOCUMENT"),
        (1, "SELFIE"),
        (2, "DOCUMENT"),
        (2, "SELFIE"),
    ]
    assert all(
        "image_base64" not in i and "vault_object_id" not in i for i in listing["items"]
    )  # never the bytes, never the vault address
    assert {i["content_type"] for i in listing["items"]} == {"image/jpeg", "image/png"}
    assert all(i["available"] for i in listing["items"])
    expires = {datetime.fromisoformat(i["expires_at"]) for i in listing["items"]}
    assert expires == {NOW + timedelta(days=3)}


def test_bytes_that_are_not_a_jpeg_or_png_are_not_kept_and_never_undo_the_rest(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    co = Company(world, owner_url)
    assert keep(rt_url, co, document=b"%PDF-1.7 not an image", selfie=JPEG) == 1
    listing = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))
    assert [i["kind"] for i in listing["items"]] == ["SELFIE"]


def test_a_photograph_opens_only_with_a_reason_and_the_opening_is_recorded_first(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    co = Company(world, owner_url)
    keep(rt_url, co)
    media_id = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["items"][0][
        "media_id"
    ]
    person = uid()

    def open_with(reason: str) -> Any:
        return run(
            rt_url,
            co.tenant,
            lambda c: media.open_one(
                c,
                tenant_id=co.tenant,
                session_id=co.session_id,
                media_id=media_id,
                principal_id=person,
                reason=reason,
                now=NOW,
            ),
        )

    with pytest.raises(PartnerError) as short:
        open_with("no")  # fewer than five characters
    assert short.value.code == "VALIDATION_FAILED"
    with pytest.raises(PartnerError):
        open_with("x" * 301)
    assert (
        run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["opened"] == []
    )  # a refused reason leaves no record and reads nothing

    view = open_with("  Customer disputes the result  ")
    assert base64.b64decode(view["image_base64"]) in (JPEG, PNG)
    opened = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["opened"]
    assert len(opened) == 1
    assert (
        opened[0]["media_id"] == media_id
        and opened[0]["by"] == person
        and opened[0]["reason"] == "Customer disputes the result"
    )
    assert opened[0]["access_id"].startswith("acc_")


def test_a_photograph_past_its_retention_or_erased_from_the_vault_cannot_be_opened(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    co = Company(world, owner_url)
    keep(rt_url, co)
    items = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["items"]
    erased, expired = items[0]["media_id"], items[1]["media_id"]

    async def age() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT vault_object_id FROM verify.api_session_media WHERE media_id = :m"
                    ),
                    {"m": erased},
                )
            ).one()
            vault.gone.add(row.vault_object_id)
            await c.execute(
                text("UPDATE verify.api_session_media SET expires_at = :past WHERE media_id = :m"),
                {"past": NOW - timedelta(hours=1), "m": expired},
            )

    asyncio.run(age())

    def open_media(media_id: str) -> Any:
        return run(
            rt_url,
            co.tenant,
            lambda c: media.open_one(
                c,
                tenant_id=co.tenant,
                session_id=co.session_id,
                media_id=media_id,
                principal_id=uid(),
                reason="Routine audit check",
                now=NOW,
            ),
        )

    for media_id in (erased, expired, "med_" + "0" * 32):
        with pytest.raises(PartnerError) as e:
            open_media(media_id)
        assert e.value.code == "NOT_FOUND"
    assert (
        run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["opened"] == []
    )  # nothing left the vault, so nothing is recorded as opened
    listed = {
        i["media_id"]: i["available"]
        for i in run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["items"]
    }
    assert listed[expired] is False


def test_another_company_sees_and_opens_nothing_and_cannot_plant_a_row_in_anothers_name(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    mine = Company(world, owner_url)
    theirs = Company(world, owner_url, tenant=uid())
    keep(rt_url, mine)
    media_id = run(rt_url, mine.tenant, lambda c: media.listing(c, mine.session_id, NOW))["items"][
        0
    ]["media_id"]

    assert (
        run(rt_url, theirs.tenant, lambda c: store.list_for_session(c, mine.session_id)) == []
    )  # row-level security hides it
    with pytest.raises(PartnerError) as e:
        run(
            rt_url,
            theirs.tenant,
            lambda c: media.open_one(
                c,
                tenant_id=theirs.tenant,
                session_id=mine.session_id,
                media_id=media_id,
                principal_id=uid(),
                reason="Looking around",
                now=NOW,
            ),
        )
    assert e.value.code == "NOT_FOUND"

    async def plant_in_anothers_name(conn: Any) -> None:
        await conn.execute(
            text(
                "INSERT INTO verify.api_session_media (media_id, session_id, tenant_id, kind, attempt, vault_object_id, content_type, byte_length, captured_at, expires_at) "
                "VALUES (:m, :s, :t, 'SELFIE', 9, 'obj_x', 'image/png', 10, now(), now() + interval '1 day')"
            ),
            {"m": "med_" + uuid.uuid4().hex, "s": theirs.session_id, "t": uuid.UUID(mine.tenant)},
        )

    with pytest.raises(
        DBAPIError
    ):  # the policy's WITH CHECK refuses a row that names a different company
        run(rt_url, theirs.tenant, plant_in_anothers_name)


def test_the_table_refuses_what_the_application_would_never_write(
    rt_url: str, owner_url: str, world: World
) -> None:
    co = Company(world, owner_url)

    async def insert(**over: Any) -> None:
        row = {
            "m": "med_" + uuid.uuid4().hex,
            "s": co.session_id,
            "t": uuid.UUID(co.tenant),
            "k": "SELFIE",
            "a": 1,
            "c": "image/png",
            "b": 10,
        }
        row.update(over)
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO verify.api_session_media (media_id, session_id, tenant_id, kind, attempt, vault_object_id, content_type, byte_length, captured_at, expires_at) "
                    "VALUES (:m, :s, :t, :k, :a, 'obj_x', :c, :b, now(), now() + interval '1 day')"
                ),
                row,
            )

    asyncio.run(
        insert()
    )  # the plain row is accepted, so each refusal below is about its one bad field
    for bad in (
        {"m": "photo-1"},
        {"k": "PASSPORT"},
        {"a": 0},
        {"c": "image/gif"},
        {"b": 0},
        {"s": "vfs_" + "0" * 32},
    ):
        with pytest.raises(IntegrityError):
            asyncio.run(insert(**bad))


def test_the_access_record_is_append_only_even_for_the_table_owner(
    rt_url: str, owner_url: str, world: World, vault: FakeVault, keep_three_days: None
) -> None:
    co = Company(world, owner_url)
    keep(rt_url, co)
    media_id = run(rt_url, co.tenant, lambda c: media.listing(c, co.session_id, NOW))["items"][0][
        "media_id"
    ]
    run(
        rt_url,
        co.tenant,
        lambda c: media.open_one(
            c,
            tenant_id=co.tenant,
            session_id=co.session_id,
            media_id=media_id,
            principal_id=uid(),
            reason="Quarterly review",
            now=NOW,
        ),
    )

    async def change(sql: str) -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text(sql))

    with pytest.raises(DBAPIError, match="append-only"):
        asyncio.run(change("UPDATE verify.api_media_access SET reason = 'something else entirely'"))
    with pytest.raises(DBAPIError, match="append-only"):
        asyncio.run(change("DELETE FROM verify.api_media_access"))
    with pytest.raises(
        IntegrityError
    ):  # a reason shorter than five characters is refused by the table, not only by the service
        asyncio.run(
            change(
                f"INSERT INTO verify.api_media_access (access_id, media_id, session_id, tenant_id, principal_id, reason) VALUES ('acc_{uuid.uuid4().hex}', '{media_id}', '{co.session_id}', '{co.tenant}', '{uid()}', 'no')"
            )
        )


# ---------------------------------------------------------------------------------------------- single approver (verify_api_0019)


def _own_decision(owner_url: str, flag: bool | None) -> None:
    """Approve a price by the very person who proposed it; `flag` is what the cell tells the database for this transaction (None: nothing)."""
    person = uid()

    async def go() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            if flag is not None:
                await single_approver.apply(c, flag)
            await c.execute(
                text(
                    "INSERT INTO verify.api_price (price_id, meter, currency, unit_rate, effective_from, status, proposed_by, decided_by, decided_at) "
                    "VALUES (:id, 'VERIFY_SESSION_START', 'GBP', 0.5, date_trunc('month', now() + (CAST(:n AS text) || ' months')::interval)::date, 'APPROVED', :p, :p, now())"
                ),
                {"id": uuid.uuid4(), "p": uuid.UUID(person), "n": str(uuid.uuid4().int % 600 + 2)},
            )

    asyncio.run(go())


def test_a_person_cannot_approve_their_own_price_unless_the_cell_set_single_approver_for_that_transaction(
    owner_url: str,
) -> None:
    with pytest.raises(IntegrityError):
        _own_decision(
            owner_url, None
        )  # the default: nothing is set, the table refuses as it always did
    with pytest.raises(IntegrityError):
        _own_decision(owner_url, False)  # the deployment's setting is off, so apply() sets nothing
    _own_decision(
        owner_url, True
    )  # the deployment chose single-approver mode and the approve path says so


def test_the_single_approver_flag_lasts_one_transaction_only(owner_url: str) -> None:
    async def go() -> list[str]:
        async with engine(owner_url) as eng:
            async with eng.begin() as c:
                await single_approver.apply(c, True)
                inside = (
                    await c.execute(
                        text("SELECT current_setting('app.verify_single_approver', true)")
                    )
                ).scalar_one()
            async with eng.begin() as c:
                after = (
                    await c.execute(
                        text(
                            "SELECT coalesce(current_setting('app.verify_single_approver', true), '')"
                        )
                    )
                ).scalar_one()
        return [inside, after]

    assert asyncio.run(go()) == [
        "on",
        "",
    ]  # it does not leak to the next transaction on the same pooled connection
