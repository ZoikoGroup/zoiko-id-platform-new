"""No live credential reaches a log (ZID-CP-WF10 v2.0 row 4-02; CR-173).

A real client secret, a real access token and a real launch link are created and USED (a good exchange, a bad guess, an API call, a refused call, a hosted page, a refused page), and everything the
process wrote to its output while that happened is searched for each of them. A credential in a log is a credential anyone with log access holds, so this must stay empty.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import ctx, engine
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import service


def test_no_secret_token_or_launch_link_is_ever_written_to_the_process_output(
    rt_url: str, capfd: pytest.CaptureFixture[str]
) -> None:
    w = World(rt_url)
    try:

        async def seed() -> dict[str, Any]:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                made = await service.create_application(
                    t,
                    w.tenant,
                    uid(),
                    name="LogScan",
                    environment="SANDBOX",
                    scopes=None,
                    origins=None,
                )
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(w.tenant)) as t:
                return {
                    "app": made,
                    "cred": await service.issue_credential(
                        t, w.tenant, uid(), made["application_id"]
                    ),
                }

        made = asyncio.run(seed())
        secret, client = made["cred"]["client_secret"], made["cred"]["client_id"]
        assert secret.startswith("zps_") and len(secret) > 20

        r = w.http.post(
            "/v1/verify-api/oauth2/token", json={"client_id": client, "client_secret": secret}
        )
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        wrong = "zps_" + "Z" * 40
        assert (
            w.http.post(
                "/v1/verify-api/oauth2/token", json={"client_id": client, "client_secret": wrong}
            ).status_code
            == 401
        )  # a bad guess is logged as a failure, never as the guess
        assert w.http.get("/v1/verify-api/whoami", headers=h).status_code == 200
        assert (
            w.http.get(
                "/v1/verify-api/whoami", headers={"Authorization": "Bearer zat_" + "Q" * 40}
            ).status_code
            == 401
        )  # a refused token
        view = w.create(h).json()
        link = w.link_token(view)
        assert w.http.get(f"/v1/verify-api/hosted/{link}").status_code == 200
        bad_link = "zvl_" + "Y" * 43
        assert w.http.get(f"/v1/verify-api/hosted/{bad_link}").status_code == 404  # a refused link
        w.http.post(
            "/v1/verify-api/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": client, "client_secret": secret},
        )  # the form and basic paths too
        w.http.get("/v1/verify-api/verification-sessions", headers=h)

        out = capfd.readouterr()
        everything = out.out + out.err
        assert len(everything) > 200, (
            "the scan saw no log output at all, so it proves nothing"
        )  # the app logs every request; an empty capture would be a false pass
        for name, value in (
            ("client secret", secret),
            ("access token", token),
            ("launch link", link),
            ("wrong secret", wrong),
            ("refused link", bad_link),
            ("bearer header", "Bearer " + token),
        ):
            assert value not in everything, f"the {name} was written to the log"
        # the client id is an identifier, not a secret, so it may appear; the secret that goes with it may not
        assert secret[4:] not in everything
    finally:
        w.close()
