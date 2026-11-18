"""A provider's retraction or correction of an article a case relied on sends that case back to a person, over the signed callback route (WF06 QA-026; CR-118), against PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from test_screening_requests_pg import NOW, load_lists, open_case, person, proposal
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.screening.application import screening_requests as sr
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp
from zid_platform.modules.screening.domain import provider_controls as pc
from zid_platform.settings import Settings

SECRET = b"provider-signing-secret"
PROVIDER = "vendor-a"


def _app(rt_url: str, o: Any) -> Any:
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1", cell_uuid=CELL),
        keyring=o.ring,
    )  # fmt: skip
    app.state.screening_provider_secrets = {PROVIDER: SECRET}
    return app


def test_a_retraction_reopens_a_case_that_relied_on_the_article_and_leaves_others_alone(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    tenant = o.tenant
    state: dict[str, str] = {}

    async def setup() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                hit = await open_case(c, tenant, "key-art-1", person("Eric Badege"))
                other = await open_case(c, tenant, "key-art-2", person("Alice Wonderland"))
                await sr.decide(
                    c,
                    hit["request_id"],
                    proposal(
                        hit["request_id"], cov.Disposition.CLEARED_FOR_PURPOSE, disp.Action.RELEASE
                    ),
                    checker="senior-1",
                    checker_role=disp.Role.SENIOR,
                    now=NOW,
                )
                state["hit"], state["other"] = hit["request_id"], other["request_id"]
        # present the case's candidate as the provider's article, as a licensed screening result would have stored it
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE verify.screening_request_candidate SET list_id = :l, source_ref = 'art-1' WHERE request_id = CAST(:r AS uuid)"),
                {"l": f"{PROVIDER}:ADVERSE_MEDIA", "r": state["hit"]},
            )  # fmt: skip

    asyncio.run(setup())

    def send(event_id: str, doc: dict[str, Any]) -> Any:
        issued = datetime.now(UTC)
        body = json.dumps({"provider_id": PROVIDER, "tenant_id": tenant, **doc}).encode()
        signature = pc.sign(SECRET, PROVIDER, tenant, event_id, issued, body)
        return api.post(
            f"/v1/integrations/screening/{tenant}.{PROVIDER}/events",
            content=body,
            headers={
                "x-zoiko-event-id": event_id,
                "x-zoiko-timestamp": issued.isoformat(),
                "x-zoiko-signature": signature,
                "content-type": "application/json",
            },
        )

    def chain(case: str) -> list[tuple[int, str, list[str]]]:
        async def read() -> list[tuple[int, str, list[str]]]:
            async with engine(owner_url) as eng, eng.connect() as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT revision, body FROM verify.screening_request_decision WHERE request_id = CAST(:r AS uuid) ORDER BY revision"
                        ),
                        {"r": case},
                    )
                ).all()
            return [(r.revision, r.body["disposition"], r.body["reason_codes"]) for r in rows]

        return asyncio.run(read())

    with TestClient(_app(rt_url, o)) as api:
        before = len(chain(state["hit"]))

        # an event of a kind nobody acts on, an unknown class, and a missing reference change nothing
        assert (
            send(
                "e-1", {"kind": "RESULT_UPDATED", "refs": ["art-1"], "class": "ADVERSE_MEDIA"}
            ).json()["cases_reopened"]
            == 0
        )
        assert (
            send(
                "e-2", {"kind": "ARTICLE_RETRACTED", "refs": ["art-1"], "class": "NOT_A_CLASS"}
            ).json()["cases_reopened"]
            == 0
        )
        assert (
            send("e-3", {"kind": "ARTICLE_RETRACTED", "class": "ADVERSE_MEDIA"}).json()[
                "cases_reopened"
            ]
            == 0
        )
        assert (
            send(
                "e-4",
                {
                    "kind": "ARTICLE_RETRACTED",
                    "refs": ["some-other-article"],
                    "class": "ADVERSE_MEDIA",
                },
            ).json()["cases_reopened"]
            == 0
        )
        assert len(chain(state["hit"])) == before

        # the retraction of the article the case relied on reopens it, by the system, with a stated reason
        r = send("e-5", {"kind": "ARTICLE_RETRACTED", "refs": ["art-1"], "class": "ADVERSE_MEDIA"})
        assert r.status_code == 202 and r.json()["cases_reopened"] == 1
        revisions = chain(state["hit"])
        assert len(revisions) == before + 1
        last = revisions[-1]
        assert last[1] == "HOLD_PENDING_REVIEW" and last[2] == [
            "ARTICLE_RETRACTED"
        ]  # held for a person; never cleared by a retraction

        # a replay changes nothing, and a case that is already waiting for a person is left as it is
        assert send(
            "e-5", {"kind": "ARTICLE_RETRACTED", "refs": ["art-1"], "class": "ADVERSE_MEDIA"}
        ).json() == {"outcome": "DUPLICATE"}
        assert (
            send(
                "e-6", {"kind": "ARTICLE_CORRECTED", "refs": ["art-1"], "class": "ADVERSE_MEDIA"}
            ).json()["cases_reopened"]
            == 0
        )
        assert len(chain(state["hit"])) == before + 1
        assert len(chain(state["other"])) == len(
            chain(state["other"])
        )  # the unrelated case was never touched
