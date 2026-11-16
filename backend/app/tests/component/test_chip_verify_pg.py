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


def test_a_chip_is_a_verified_birth_date_source_for_age_checks_and_says_it_does_not_bind_the_holder(
    rt_url: str,
) -> None:
    """DTV-19: the chip's birth date counts only after passive authentication; no date is ever echoed."""
    chip = make_chip()  # the fixture person was born 1974-08-12
    with world(rt_url) as (http, mine, _tenant, _admin):

        def age(threshold: int, **evidence) -> dict:
            r = http.post("/v2/age-checks", headers=mine, json={"threshold": threshold, **evidence})
            assert r.status_code == 200, r.text
            assert "1974" not in r.text, "the birth date is never echoed"
            return r.json()

        chip_in = {"chip": body(chip)}

        # no country signing CA configured: the chip proves nothing, and the person is offered a documented check
        none = age(18, **chip_in)
        assert none["result"] == "UNDETERMINED" and none["fallback_offered"] is True
        assert none["reason_codes"][0] == "CHIP_NOT_VERIFIED"

        http.app.state.trust_config = TrustConfig(csca=(chip.csca.cert,))
        adult = age(18, **chip_in)
        assert adult["result"] == "AT_LEAST" and adult["method"] == "VERIFIED_BIRTH_DATE"
        assert adult["valid_until"] is None
        assert adult["reason_codes"] == ["HOLDER_NOT_BOUND", "CLONE_NOT_DETECTED"]
        assert any(
            "not that the person presenting it is the holder" in x for x in adult["limitations"]
        )
        assert age(65, **chip_in)["result"] == "UNDER"

        # a typed date beside the chip never overrides it
        both = age(18, **chip_in, birth_date="2015-01-01")
        assert both["result"] == "AT_LEAST" and both["method"] == "VERIFIED_BIRTH_DATE"

        # a changed data group is not verified, so the chip decides nothing
        forged = {**chip.data_groups, 1: dg1(mrz_td3(surname="IMPOSTER"))}
        bad = age(18, chip=body(chip, forged))
        assert bad["result"] == "UNDETERMINED" and "CHIP_NOT_VERIFIED" in bad["reason_codes"]

        # a chip that failed does not stop a clear estimate from deciding
        mixed = age(
            18,
            **chip_in | {"chip": body(chip, forged)},
            estimate={"estimated_age": 45, "margin": 3},
        )
        assert mixed["result"] == "AT_LEAST" and mixed["method"] == "ESTIMATION"

        assert http.post("/v2/age-checks", headers=mine, json={"threshold": 18}).status_code == 422
        assert (
            http.post(
                "/v2/age-checks",
                headers=mine,
                json={
                    "threshold": 18,
                    "chip": {"sod_base64": "!!bad!!", "data_groups": {"1": "AAAA"}},
                },
            ).status_code
            == 422
        )
