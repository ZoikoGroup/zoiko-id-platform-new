"""No global background monitoring by default, on PostgreSQL (ZID-RCG-003 RCG-G12; CR-081): the scheduled sanctions re-screen runs only where a
monitoring approval covers the case's country. A case outside one is held (nothing screened, nothing appended) and screened as usual once covered."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from pack_test_support import captured
from test_screening_passes_pg import EU, NOW, SDN, UK, sign, un
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import monitoring_gate as gate
from zid_platform.modules.screening.application import passes
from zid_platform.modules.screening.application import screening as service
from zid_platform.modules.screening.domain import monitoring_scope as ms
from zid_platform.modules.screening.domain.decision import AuthorityEvidence, AuthorityKind, Role
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Graph, Holder, HolderKind
from zid_platform.modules.screening.infrastructure.config import config


def _approval(country: str) -> ms.MonitoringApproval:
    a = ms.MonitoringApproval(
        approval_id=f"{country.lower()}-sanctions",
        version=1,
        country=country,
        kind=ms.Kind.SANCTIONS_ONGOING,
        sectors=frozenset({"ANY"}),
        legal_basis_ref="LEGAL-MLR2017",
        contract_ref="CONTRACT-2027",
        valid_from=NOW - timedelta(days=30),
    )
    digest = ms.approval_hash(a)
    return replace(a, approvals={r: ms.Approval(f"REF-{r}", digest) for r in ms.APPROVER_ROLES})


@pytest.fixture
def scope() -> Any:
    before = gate.mode()
    mp = pytest.MonkeyPatch()
    approvals: list[ms.MonitoringApproval] = []
    mp.setattr(gate, "monitoring_approvals", lambda: tuple(approvals))

    def set_(mode: str, *a: ms.MonitoringApproval) -> None:
        gate.configure(mode)
        approvals[:] = list(a)

    yield set_
    gate.configure(before)
    mp.undo()


def _monitoring(at: Any) -> Any:
    def check(country: str) -> None:  # what the worker passes
        gate.require_monitoring(country=country, kind=ms.Kind.SANCTIONS_ONGOING, at=at)

    return check


def test_the_rescreen_runs_only_where_ongoing_monitoring_is_approved(
    rt_url: str, owner_url: str, scope: Any
) -> None:
    tenant, requester = str(uuid.uuid4()), str(uuid.uuid4())
    cfg = config()
    graph = Graph("co", {"co": Holder("co", HolderKind.ENTITY)}, ())
    subjects = [
        (Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        (Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, "Alice Smith")),
    ]
    gathered = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-1")

    async def lists(eng: Any, un_bytes: bytes) -> None:
        async with eng.begin() as c:
            for lid, files in (
                (ListId.OFAC_SDN, {"sdn": SDN}), (ListId.OFAC_NON_SDN, {"sdn": SDN}), (ListId.UK_SANCTIONS, {"file": UK}),
                (ListId.EU_CONSOLIDATED, {"file": EU}), (ListId.UN_SC, {"file": un_bytes}),
            ):  # fmt: skip
                await service.ingest_list(c, lid, files, fetched_at=NOW, source_ref="t", now=NOW)

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            await lists(eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd")))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.open_case(
                    c,
                    tenant,
                    country="GB",
                    subjects=subjects,
                    graph=graph,
                    authority=gathered,
                    requested_by=requester,
                    idempotency_key=f"scope-{uuid.uuid4().hex[:8]}",
                    cfg=cfg,
                    sign=sign,
                    now=NOW,
                )  # fmt: skip  (onboarding screening is not background monitoring: it runs with no approval at all)
                case_id = v.case_id
            await lists(
                eng, un(("Eric", "Badege"), ("Aaa", "Bbb"), ("Ccc", "Ddd"), ("Alice", "Smith"))
            )  # the case is now due

            async def rescreen(minutes: int) -> dict[str, int]:
                at = NOW + timedelta(minutes=minutes)
                async with worker.begin() as c:
                    return await passes.rescreen_pass(
                        c,
                        cfg,
                        sign,
                        at,
                        country_gate=lambda _c: None,
                        monitoring_gate=_monitoring(at),
                    )

            async def versions() -> list[int]:
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    return [r.version for r in await service.history(c, tenant, case_id)]

            scope("enforce")  # nothing approved: no background monitoring
            held = await rescreen(10)
            assert held[tenant] == 0 and held[f"{tenant}:held_by_monitoring_scope"] == 1
            assert await versions() == [1]  # nothing screened, nothing appended

            scope("enforce", _approval("FR"))  # an approval for another country does not cover GB
            assert (await rescreen(20))[f"{tenant}:held_by_monitoring_scope"] == 1

            scope("shadow")  # shadow reports what it would hold and screens as before
            with captured("zid.regulatory") as logs:
                done = await rescreen(30)
            assert done[tenant] == 1 and f"{tenant}:held_by_monitoring_scope" not in done
            assert any(
                "monitoring scope would hold (shadow)" in m
                and "SANCTIONS_ONGOING" in m
                and "GB" in m
                for m in logs
            )
            assert await versions() == [1, 2]

            await lists(
                eng,
                un(
                    ("Eric", "Badege"),
                    ("Aaa", "Bbb"),
                    ("Ccc", "Ddd"),
                    ("Alice", "Smith"),
                    ("Bob", "Jones"),
                ),
            )  # due again
            scope("enforce", _approval("GB"))  # approved: screened as usual
            done = await rescreen(40)
            assert done[tenant] == 1 and f"{tenant}:held_by_monitoring_scope" not in done
            assert await versions() == [1, 2, 3]

    asyncio.run(run())
