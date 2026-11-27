"""The governed metric catalogue (ZID-CPS-WF-02 section 05, 07.11). Every published figure resolves to one of these: a name, an owner, a version,
the numerator and denominator in words, the population and time basis, the exclusions and the source events. Changing a formula means a new
version, never an edit of an old one, so a figure exported under 1.0 can always be explained under 1.0.

Definitions that rest on an owner decision say so (D-02 semantics, D-03 cost allocation, D-05 SLI eligibility); until it is made they are
the conservative reading below and are labelled `PROPOSED`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Metric:
    metric_id: str
    name: str
    version: str
    owner: str
    unit: str  # count | ratio | seconds | money
    numerator: str
    denominator: str
    population: str
    exclusions: tuple[str, ...]
    sources: tuple[str, ...]
    status: str = "PROPOSED"  # PROPOSED until the owners sign it (G0)
    finance: bool = False  # only a person with billing access sees it

    def view(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id, "name": self.name, "version": self.version, "owner": self.owner, "unit": self.unit,
            "numerator": self.numerator, "denominator": self.denominator, "population": self.population, "exclusions": list(self.exclusions),
            "sources": list(self.sources), "status": self.status, "finance": self.finance,
        }  # fmt: skip


SESSION = "Hosted sessions created in the window (session.created time), in the selected environment and market"
MATURE = "Only sessions whose link has expired by the time of the query (the maturity horizon), so a recent session is never counted as lost"

CATALOGUE: dict[str, Metric] = {
    m.metric_id: m
    for m in (
        Metric(
            "M01",
            "Sessions created",
            "1.0",
            "Product",
            "count",
            "Distinct sessions with session.created",
            "-",
            SESSION,
            ("Sandbox and production are never mixed",),
            ("verification.session.created",),
        ),
        Metric(
            "M02",
            "Start rate",
            "1.0",
            "Product",
            "ratio",
            "Matured sessions with a first capture started",
            "Matured sessions created",
            f"{SESSION}. {MATURE}",
            (
                "A session with no capture event is NOT_STARTED; missing instrumentation is UNKNOWN, never abandonment",
            ),
            ("verification.session.created", "verification.session.capture_started"),
        ),
        Metric(
            "M03",
            "Submission rate",
            "1.0",
            "Product",
            "ratio",
            "Started sessions the person submitted",
            "Matured sessions with a capture started",
            f"{SESSION}. {MATURE}",
            ("Several attempts in one session count once",),
            ("verification.session.capture_started", "verification.session.submitted"),
        ),
        Metric(
            "M04",
            "Verification decision rate",
            "1.0",
            "Product",
            "ratio",
            "Submitted sessions with a final decision",
            "Submitted sessions",
            SESSION,
            ("A session waiting for a reviewer is pending, never a synthetic decision",),
            ("verification.session.submitted", "verification.session.finalized"),
        ),
        Metric(
            "M05",
            "Automated decision rate",
            "1.0",
            "Risk",
            "ratio",
            "Final decisions the platform reached with no reviewer",
            "All final decisions",
            SESSION,
            (
                "A decision with any reviewer signature is human; there is no target: approvals are never optimised past a needed review",
            ),
            ("verification.session.finalized",),
        ),
        Metric(
            "M06",
            "First-attempt completion",
            "1.0",
            "Product",
            "ratio",
            "Sessions whose first capture attempt was accepted",
            "Sessions with at least one capture attempt",
            SESSION,
            (
                "A resume without a new capture is the same attempt; a technical failure is not a person's failure",
            ),
            ("verification.session.capture_attempt_completed",),
        ),
        Metric(
            "M07",
            "Decision processing time",
            "1.0",
            "Risk",
            "seconds",
            "Final decision time minus submission time, p50 and p95, automated decisions only",
            "Automated final decisions",
            SESSION,
            ("Reviewed decisions are measured by M09, not here",),
            ("verification.session.submitted", "verification.session.finalized"),
        ),
        Metric(
            "M08",
            "End-to-end journey time",
            "1.0",
            "Product",
            "seconds",
            "Final decision time minus first notice shown, p50 and p95",
            "Final decisions with both times",
            SESSION,
            ("A session with a missing time is excluded and counted as such",),
            ("verification.session.notice_presented", "verification.session.finalized"),
        ),
        Metric(
            "M09",
            "Review SLA breach",
            "1.0",
            "Operations",
            "ratio",
            "Review cases past their due time without a decision, or decided after it",
            "Review cases whose due time has passed",
            SESSION,
            (
                "Due = review needed + the market's review SLA; no pause mechanism exists, so none is applied",
            ),
            ("verification.session.review_required", "verification.session.finalized"),
        ),
        Metric(
            "M10",
            "Risk signals and findings",
            "1.0",
            "Risk",
            "count",
            "Three separate series: capture risk signals, reviewer-confirmed findings, overturned findings",
            "-",
            SESSION,
            ("A signal is not fraud prevented; nothing is called fraud blocked",),
            ("verification.session.media_accepted", "verification.session.finalized"),
        ),
        Metric(
            "M11",
            "Provider technical error rate",
            "1.0",
            "Operations",
            "ratio",
            "Provider checks that failed technically",
            "Provider checks attempted",
            "Provider checks in the window",
            ("A policy denial is not a technical error",),
            ("verification.idv.provider_check_finished",),
        ),
        Metric(
            "M12",
            "Webhook final delivery",
            "1.0",
            "Engineering",
            "ratio",
            "Deliveries acknowledged with 2xx by the recipient",
            "Deliveries that reached a final state",
            "Webhook deliveries finished in the window",
            ("Queued or attempted is not delivered; a retried event counts once",),
            ("webhook.delivery.succeeded", "webhook.delivery.failed", "webhook.delivery.exhausted"),
        ),
        Metric(
            "M13",
            "Cost per approved subject",
            "1.0",
            "Finance",
            "money",
            "Attributable incurred verification cost",
            "Distinct approved subjects",
            SESSION,
            (
                "Never the tenant's invoice price; incomplete costs are provisional; allocation awaits D-03",
            ),
            ("commercial vendor cost ledger", "verification.session.finalized"),
            finance=True,
        ),
        Metric(
            "M14",
            "Realized customer yield",
            "1.0",
            "Finance",
            "money",
            "Net rated verification fees",
            "Billable verification units",
            "IDV_SUBMISSION usage in the window",
            ("The billing system is authoritative; no price means no yield, never zero",),
            ("commercial billable ledger",),
            finance=True,
        ),
        Metric(
            "M15",
            "API availability",
            "1.0",
            "SRE",
            "ratio",
            "API calls answered without a server error",
            "API calls that were valid requests",
            "Usage in the window",
            ("Client errors (4xx) are not counted against availability; eligibility awaits D-05",),
            ("metering usage ledger",),
        ),
        Metric(
            "M16",
            "Analytics freshness",
            "1.0",
            "Data Platform",
            "seconds",
            "Now minus the newest ingested event time, per source",
            "-",
            "Each source domain",
            ("A source with no events is NO_DATA, never fresh",),
            ("ingestion watermark",),
        ),
    )
}


def get(metric_id: str) -> Metric:
    return CATALOGUE[metric_id]
