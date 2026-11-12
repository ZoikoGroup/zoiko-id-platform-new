"""AT-019: when a person clears a signal that was raised earlier, whatever relied on it is told to decide again (ZID-PRI-004 section 22).

There is no linked-identity graph and none is built here. The signals the platform does raise (repeated sign-ups from one hashed device, contact or
network key) are the nearest thing to a link, and a correction of one is announced; a ruling that CONFIRMS or escalates a risk announces no correction.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_screening_signals_api_pg import QUEUE, REVIEW_ACTIONS
from test_v2_events_pg import events
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.screening.ports import AttemptAction, note_attempt
from zid_platform.settings import Settings


def test_at019_clearing_a_signal_announces_a_correction_and_confirming_one_does_not(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=REVIEW_ACTIONS, extra_resources=["screening_signal:*"])
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_id="uk-1", cell_uuid=CELL,
        ),
        keyring=o.ring,
    )  # fmt: skip
    now = datetime.now(UTC)

    async def breach() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            for device in ("device-1", "device-2"):  # two breaches, two review items
                for _ in range(3):
                    await note_attempt(
                        c, o.tenant, subject_ref=f"registration:{device}", action=AttemptAction.SIGNUP,
                        now=now, device=device,
                    )  # fmt: skip

    asyncio.run(breach())
    h = o.bearer(o.owner)
    with TestClient(app, base_url=ISS) as http:
        items = http.get(QUEUE, headers=h).json()["items"]
        assert len(items) == 2
        first, second = (i["review_id"] for i in items)
        assert events(rt_url, o.tenant, "screening.signal.corrected") == [], (
            "nothing is corrected while the items are open"
        )

        def rule(review_id: str, outcome: str):
            return http.post(
                f"{QUEUE}/{review_id}/resolution", headers=h,
                json={"outcome": outcome, "rationale": "Checked the account history and the device records in full.", "evidence_ref": "case-77"},
            )  # fmt: skip

        assert rule(second, "CONFIRMED_RISK").status_code == 200
        assert events(rt_url, o.tenant, "screening.signal.corrected") == [], (
            "confirming a risk corrects nothing"
        )

        cleared = rule(first, "CLEARED_BY_REVIEWER")
        assert cleared.status_code == 200, cleared.text
        sent = events(rt_url, o.tenant, "screening.signal.corrected")
        assert len(sent) == 1
        data = sent[0].get("data", sent[0])
        assert data["review_id"] == first and data["outcome"] == "CLEARED_BY_REVIEWER"
        assert data["supersedes_event"] == "screening.signal.raised"
        assert data["action_required"] == "RECOMPUTE_DECISIONS_THAT_RELIED_ON_THIS_SIGNAL"
        flat = json.dumps(sent)
        assert (
            "device-1" not in flat and "Checked the account" not in flat and "case-77" not in flat
        ), "ids and codes only"
        assert rule(first, "CLEARED_BY_REVIEWER").status_code in (409, 422), (
            "a ruled item is not ruled twice"
        )
        assert len(events(rt_url, o.tenant, "screening.signal.corrected")) == 1
