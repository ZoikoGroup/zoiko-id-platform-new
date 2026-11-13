"""POST /v2/documents/chip-verify through the real app and Postgres: passive authentication of a passport chip (ICAO Doc 9303), stateless.

The shipped configuration names no country signing CA, so a genuine chip is INDETERMINATE until an operator configures one. With one configured the
best answer is VERIFIED_LIMITED, and the holder is never bound by this check.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from test_v2_facade_pg import world
from zid_platform.modules.evidence.application.trust import TrustConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from epassport_fixture import dg1, make_chip, mrz_td3


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def body(chip, groups=None) -> dict:
    groups = chip.data_groups if groups is None else groups
    return {"sod_base64": b64(chip.sod), "data_groups": {str(n): b64(g) for n, g in groups.items()}}


def test_a_chip_is_indeterminate_until_a_country_is_configured_then_verified_limited_and_never_bound(
    rt_url: str,
) -> None:
    chip = make_chip()
    with world(rt_url) as (http, mine, _tenant, _admin):
        assert http.post("/v2/documents/chip-verify", json=body(chip)).status_code == 401

        shipped = http.post("/v2/documents/chip-verify", headers=mine, json=body(chip))
        assert shipped.status_code == 200, shipped.text
        out = shipped.json()
        assert out["status"] == "INDETERMINATE" and out["reasons"] == ["NO_CSCA_CONFIGURED"]
        assert out["csca_configured"] is False and out["document_authentic"] is False
        assert out["assurance_ceiling"] == "SELF_DECLARED"

        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        ok = http.post("/v2/documents/chip-verify", headers=mine, json=body(chip)).json()
        assert ok["status"] == "VERIFIED_LIMITED" and ok["document_authentic"] is True
        assert ok["holder_bound"] is False and "HOLDER_NOT_BOUND" in ok["reasons"]
        assert ok["assurance_ceiling"] == "ISSUER_AUTHENTICATED"
        assert ok["authenticity"] == "DOCUMENT_INTEGRITY"
        assert ok["document"]["surname"] == "ERIKSSON" and ok["data_groups_verified"] == [1, 2, 14]
        assert "pretend face image" not in str(ok), "the photograph is never returned"

        # a data group changed after signing is not verified, even with a correct MRZ check digit
        forged = {**chip.data_groups, 1: dg1(mrz_td3(surname="IMPOSTER"))}
        bad = http.post("/v2/documents/chip-verify", headers=mine, json=body(chip, forged)).json()
        assert bad["status"] == "NOT_VERIFIED" and bad["reasons"] == ["DATA_GROUP_HASH_MISMATCH"]
        assert bad["document_authentic"] is False and bad["assurance_ceiling"] == "SELF_DECLARED"

        # a country the deployment does not trust
        http.app.state.trust_config = TrustConfig(csca=(make_chip().csca.cert,))
        other = http.post("/v2/documents/chip-verify", headers=mine, json=body(chip)).json()
        assert other["status"] == "INDETERMINATE"
        assert other["reasons"] == ["SIGNER_NOT_ISSUED_BY_A_CONFIGURED_CSCA"]


def test_malformed_requests_are_refused_and_nothing_is_stored(rt_url: str) -> None:
    chip = make_chip()
    with world(rt_url) as (http, mine, _tenant, _admin):

        def post(payload: dict):
            return http.post("/v2/documents/chip-verify", headers=mine, json=payload)

        assert post({**body(chip), "sod_base64": "!!not base64!!"}).status_code == 422
        assert post({**body(chip), "data_groups": {}}).status_code == 422
        assert post({**body(chip), "data_groups": {"99": b64(b"x" * 8)}}).status_code == 422
        assert post({**body(chip), "unknown": 1}).status_code == 422
        junk = post({"sod_base64": b64(b"not a sod at all"), "data_groups": {"1": b64(b"abcd")}})
        assert junk.status_code == 200 and junk.json()["document_authentic"] is False
