"""Rights requests over screening data on PostgreSQL (WF06 section 17, QA-042; CR-116): found, exported with the result withheld, and disposed by scrubbing while the decision chain stays."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import NOW, load_lists, open_case, person, request, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.infrastructure import privacy_adapter as pa


def count(c: Any, sql: str, **p: Any):
    return c.execute(text(sql), p)


def test_a_persons_cases_are_found_exported_without_the_result_and_scrubbed_while_the_chain_stays(
    rt_url: str,
) -> None:
    tenant, principal, other_principal = uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(
                    c,
                    tenant,
                    "k-privacy-1",
                    person("Eric Badege"),
                    request=request(tenant, subject_ref=principal),
                )
                await open_case(
                    c,
                    tenant,
                    "k-privacy-2",
                    person("Someone Else"),
                    request=request(tenant, subject_ref=other_principal),
                )
                case_id = hit["case_id"] if "case_id" in hit else hit["request_id"]

                found = await pa._locate(c, principal)
                assert [f["object_ref"] for f in found] == [case_id]
                assert (
                    found[0]["object_type"] == "screening.request" and found[0]["region"] == "uk-1"
                )
                assert await pa._locate(c, uid()) == []  # a stranger has nothing here

                entry = SimpleNamespace(object_ref=case_id)
                shown = await pa._export(c, entry)
                assert shown["screening_performed"] is True and shown["result"] == "WITHHELD"
                assert shown["details_you_gave"]["name"] == "Eric Badege"
                for secret in ("candidate", "matched", "score", "UN_SC", "disposition", "reasons"):
                    assert secret not in repr(shown).replace("WITHHELD", ""), secret

                chain = (
                    await count(
                        c,
                        "SELECT count(*) FROM verify.screening_request_decision WHERE request_id = :r",
                        r=case_id,
                    )
                ).scalar_one()
                candidates = (
                    await count(
                        c,
                        "SELECT count(*) FROM verify.screening_request_candidate WHERE request_id = :r",
                        r=case_id,
                    )
                ).scalar_one()
                assert candidates >= 1

                await pa._dispose(c, entry, "ERASE")
                await pa._dispose(c, entry, "ERASE")  # idempotent

                subject = (
                    await count(
                        c,
                        "SELECT name, birth, countries, identifiers FROM verify.screening_request_subject WHERE request_id = :r",
                        r=case_id,
                    )
                ).one()
                assert (
                    subject.name == pa.ERASED
                    and subject.birth is None
                    and subject.countries == []
                    and subject.identifiers == []
                )
                names = (
                    await count(
                        c,
                        "SELECT matched_name, reasons, programs FROM verify.screening_request_candidate WHERE request_id = :r",
                        r=case_id,
                    )
                ).all()
                assert all(
                    n.matched_name == pa.ERASED and n.reasons == [] and n.programs == []
                    for n in names
                )
                assert (
                    await count(
                        c,
                        "SELECT count(*) FROM verify.screening_request_decision WHERE request_id = :r",
                        r=case_id,
                    )
                ).scalar_one() == chain  # the chain is untouched
                assert (
                    await count(
                        c,
                        "SELECT count(*) FROM verify.screening_request WHERE request_id = :r",
                        r=case_id,
                    )
                ).scalar_one() == 1  # and so is the case row

                # the other person's case was not touched
                other = (
                    await count(
                        c,
                        "SELECT name FROM verify.screening_request_subject s JOIN verify.screening_request r USING (request_id) WHERE r.subject_ref = :s",
                        s=other_principal,
                    )
                ).scalar_one()
                assert other == "Someone Else"

    asyncio.run(run())


def test_the_adapter_is_registered_in_the_privacy_data_map() -> None:
    from zid_platform.modules.privacy.application.service import register_store, registered_stores

    pa.register(register_store)
    assert pa.STORE in registered_stores()
