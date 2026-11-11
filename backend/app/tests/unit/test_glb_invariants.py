"""Structural invariants from ZID-GAP-001 v2.0 that no behavioural test can show directly.

GLB-25  a shared proxy or public IP that raises a fraud score never causes an automated professional rejection.
        The strongest guarantee is structural: no credential decision function takes a risk, fraud, score, device or network
        signal as an input, so one cannot influence the outcome. This test keeps it that way: adding such a parameter fails here and
        needs a deliberate decision (a human review path and an appeal) before the test is changed.
"""

from __future__ import annotations

import inspect
import re

from zid_platform.modules.credential.application import claim_checks, trust_service
from zid_platform.modules.credential.domain import claims, eligibility, standing, trust_graph

RISK_WORDS = re.compile(
    r"risk|fraud|score|signal|device|velocity|ip_|_ip|network|proxy|geoip", re.IGNORECASE
)

DECISION_FUNCTIONS = [
    claims.decide,
    claims.assertion_from_facts,
    claims.unqueried_assertion,
    eligibility.evaluate,
    eligibility.engagement_check,
    eligibility.tier_of,
    standing.license_standing,
    standing.certificate_status,
    standing.cpd_standing,
    standing.disciplinary_result,
    standing.insurance_result,
    trust_graph.trusted_for,
    trust_graph.accreditation_at_award,
    trust_graph.recognition,
    claim_checks.run_check,
    claim_checks.run_disciplinary_check,
    trust_service.evaluate_eligibility,
    trust_service.evaluate_engagement,
]


def test_glb_25_no_credential_decision_takes_a_risk_or_fraud_signal_as_input():
    offenders = {
        f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}": [
            p for p in inspect.signature(fn).parameters if RISK_WORDS.search(p)
        ]
        for fn in DECISION_FUNCTIONS
    }
    assert not {k: v for k, v in offenders.items() if v}, offenders


def test_glb_25_the_engagement_reasons_are_only_evidence_based():
    """Every reason an engagement or eligibility decision can give names a credential, an identity, an insurance or a restriction fact
    (never a score or a signal), so an appeal always has something concrete to contest."""
    source = inspect.getsource(eligibility)
    emitted = set(re.findall(r'"([A-Z][A-Z_]{5,})"', source))
    assert not {c for c in emitted if RISK_WORDS.search(c)}, emitted
