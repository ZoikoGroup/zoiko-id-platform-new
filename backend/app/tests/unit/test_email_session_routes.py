"""ZID-EM-022 and ZID-EM-088 are wired to events the platform emits, and say no more than the event knows (Communication Estate §03)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from zid_platform.modules.communication.application import dispatch
from zid_platform.modules.communication.application.dispatch import BuildContext

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _ctx(data: dict[str, Any]) -> BuildContext:
    return BuildContext(conn=None, tenant_id="t", event_time=NOW, data=data, event_id="e")  # type: ignore[arg-type]


def test_a_routine_sign_out_or_a_token_rotation_sends_no_session_mail() -> None:
    route = dispatch.EVENT_ROUTES["session.revoked"][0]
    assert not route.when({"reason": "logout"}) and not route.when({"reason": "rotated"})
    assert route.when({"reason": "global_logout"}) and route.when({"reason": "caep:compromise"})
    assert asyncio.run(dispatch._sessions_revoked(_ctx({"reason": "logout"}))) is None


def test_the_session_mail_names_a_known_cause_and_never_echoes_the_raw_reason() -> None:
    admin = asyncio.run(dispatch._sessions_revoked(_ctx({"reason": "admin:0190-secret-id"})))
    unknown = asyncio.run(dispatch._sessions_revoked(_ctx({"reason": "rule:x/y"})))
    assert admin is not None and unknown is not None
    assert "administrator" in str(admin["reason_public_label"])
    assert "0190-secret-id" not in str(admin) and "rule:x/y" not in str(unknown)
    assert unknown["reason_public_label"] == "a security event was detected on the account"


def test_both_new_routes_use_template_fields_that_exist() -> None:
    from zid_platform.modules.communication.infrastructure.catalogue_file import load

    cat = load()
    for name, tid in (("session.revoked", "ZID-EM-022"), ("tenant.created", "ZID-EM-088")):
        assert [r.template_id for r in dispatch.EVENT_ROUTES[name]] == [tid]
        assert cat.get(tid).event
    merge = asyncio.run(dispatch._sessions_revoked(_ctx({"reason": "global_logout"})))
    assert merge is not None and set(merge) <= cat.get("ZID-EM-022").fields
