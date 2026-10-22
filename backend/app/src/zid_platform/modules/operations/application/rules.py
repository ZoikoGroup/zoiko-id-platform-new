"""Prometheus recording and alerting rules rendered from the service catalogue (ZID-SRE-001
§5-6; OPS-03/04; SRE-03). The burn windows are zid_telemetry.slo.WINDOWS, the same ones the
in-process `/ops/services/{id}/slos` view evaluates."""

from __future__ import annotations

from typing import Any

from zid_telemetry.slo import WINDOWS, AlertClass

from zid_platform.modules.operations.domain.catalogue import Catalogue

_RATE_WINDOWS = sorted({w.long_s for w in WINDOWS} | {w.short_s for w in WINDOWS})


def _dur(s: int) -> str:
    return (
        f"{s // 86400}d" if s % 86400 == 0 else f"{s // 3600}h" if s % 3600 == 0 else f"{s // 60}m"
    )


def runbook_url(base: str, runbook: str) -> str:
    return f"{base.rstrip('/')}/{runbook}.md"


def render_rules(cat: Catalogue, *, runbook_base: str) -> dict[str, Any]:
    """Prometheus rule groups: per-window SLI error ratios, multi-window burn-rate alerts for
    every SLO, and static alerts for the zero-budget invariants. Every alert carries owner,
    runbook and dashboard (OPS-04) and its alert class (PAGE/TICKET)."""
    recording = []
    for seconds in _RATE_WINDOWS:
        d = _dur(seconds)
        recording.append(
            {
                "record": f"zid:sli_error_ratio:rate{d}",
                "expr": (
                    f'sum by (cell, journey, sli) (rate(zid_sli_events_total{{outcome="bad"}}[{d}]))'
                    f" / sum by (cell, journey, sli) (rate(zid_sli_events_total[{d}]))"
                ),
            }
        )
    alerts = []
    for svc in cat.services.values():
        for slo in svc.slos:
            budget = round(1.0 - slo.target, 10)
            sel = f'{{journey="{svc.service_id}", sli="{slo.sli}"}}'
            for w in WINDOWS:
                long_d, short_d = _dur(w.long_s), _dur(w.short_s)
                alerts.append(
                    {
                        "alert": f"SLOBurn_{slo.slo_id.replace('-', '_')}_{long_d}",
                        "expr": (
                            f"(zid:sli_error_ratio:rate{long_d}{sel} > ({w.factor} * {budget}))"
                            f" and (zid:sli_error_ratio:rate{short_d}{sel} > ({w.factor} * {budget}))"
                        ),
                        "for": "2m" if w.alert is AlertClass.PAGE else "15m",
                        "labels": {
                            "class": w.alert.value,
                            "service": svc.service_id,
                            "tier": svc.tier,
                            "slo": slo.slo_id,
                            "window": f"{long_d}/{short_d}",
                        },
                        "annotations": {
                            "summary": f"{svc.name}: {slo.sli} error budget burning at >= {w.factor}x",
                            "owner": svc.owner,
                            "oncall": svc.oncall,
                            "runbook_url": runbook_url(runbook_base, svc.runbooks[0]),
                            "dashboard": svc.dashboard,
                        },
                    }
                )
    invariant_alerts = [
        {
            "alert": f"Invariant_{i.invariant_id.replace('-', '_')}",
            "expr": i.expr,
            "labels": {
                "class": i.alert,
                "severity_hypothesis": i.severity_hypothesis,
                "invariant": i.invariant_id,
            },
            "annotations": {
                "summary": i.summary,
                "owner": i.owner,
                "runbook_url": runbook_url(runbook_base, i.runbook),
                "dashboard": i.dashboard,
            },
        }
        for i in cat.invariants
    ]
    return {
        "groups": [
            {"name": "zid-sli-recording", "interval": "30s", "rules": recording},
            {"name": "zid-slo-burn", "rules": alerts},
            {"name": "zid-invariants", "rules": invariant_alerts},
        ]
    }
