"""The method certification board over HTTP on PostgreSQL (WF03 P10; CR-119): honest levels from the real trust store and configuration, nothing approved by default, and no access without a session."""

from __future__ import annotations

from epassport_fixture import make_chip
from fastapi.testclient import TestClient
from test_self_run_session_pg import ISS, world
from zid_platform.modules.evidence.application.trust import TrustConfig

ROUTE = "/verification-method-certification"


def test_the_board_reports_each_method_honestly_and_approves_nothing_by_default(
    rt_url: str,
) -> None:
    chip = make_chip()
    app, _alice, _adm, svc = world(rt_url)
    with TestClient(app, base_url=ISS) as http:
        assert http.get(ROUTE).status_code in (401, 403)  # no session, no board

        out = http.get(ROUTE, headers=svc)
        assert out.status_code == 200, out.text
        board = {m["method"]: m for m in out.json()["methods"]}
        assert set(board) == {
            "CHIP_PASSIVE_AUTHENTICATION", "CHIP_CLONE_CHECK", "PRINTED_DOCUMENT_QUALITY",
            "PRINTED_DOCUMENT_AUTHENTICITY", "FACE_AND_LIVENESS_ENGINE", "DEVICE_ATTESTATION",
        }  # fmt: skip
        assert out.json()["counts"]["APPROVED"] == 0  # the shipped configuration approves nothing
        assert board["PRINTED_DOCUMENT_AUTHENTICITY"]["level"] == "NOT_BUILT"
        assert (
            board["FACE_AND_LIVENESS_ENGINE"]["level"] == "EVIDENCE_INCOMPLETE"
            and board["FACE_AND_LIVENESS_ENGINE"]["needs"]
        )
        assert (
            board["CHIP_PASSIVE_AUTHENTICATION"]["level"] == "EVIDENCE_INCOMPLETE"
        )  # no country list is active

        # with a country's signing authority in force the chip check is built-but-unproven, never approved
        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        again = {m["method"]: m for m in http.get(ROUTE, headers=svc).json()["methods"]}
        assert again["CHIP_PASSIVE_AUTHENTICATION"]["level"] == "BUILT_UNPROVEN"
        assert again["CHIP_PASSIVE_AUTHENTICATION"][
            "needs"
        ]  # a real passport and phone are still outstanding
