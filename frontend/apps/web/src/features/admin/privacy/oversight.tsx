"use client";

import { Banner, Card, Stat } from "@zoiko/ui";
import { CheckCircle2 } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Biometric governance monitor (ZID-DTV-WF-04 B12; CR-110). Counts from the records for this organization and what needs attention, with the
 * tab to go to. No person, image, score or template appears here, and nothing here can identify anyone. Refreshes every minute. */

/** What each attention code means and where to act. */
export const ATTENTION: Record<string, { text: string; where: string }> = {
  BREACH_PAUSING_CAPTURE: { text: "An open breach case is pausing biometric capture.", where: "Breaches" },
  DPIA_LAPSED: { text: "An approved DPIA has lapsed: the biometric method resting on it has stopped.", where: "DPIA" },
  DPIA_EXPIRING: { text: "An approved DPIA ends within 30 days. Reassess it before the method stops.", where: "DPIA" },
  MODEL_HELD: { text: "A model is held (drift or a stale evaluation): methods on it are off.", where: "Models & fairness" },
  REVIEWS_OVERDUE: { text: "Appeals or reviews are past their due date.", where: "Appeals & reviews" },
  ERASURE_STUCK: { text: "An erasure job failed, or a provider has not confirmed a deletion in time.", where: "Erasure" },
  SUBPROCESSOR_CHANGE_WAITING: { text: "A processor change is waiting: transfers to that processor are blocked.", where: "Transfers & subprocessors" },
  RETENTION_MISSING: { text: "Some biometric asset classes have no retention period: methods producing them stay off.", where: "Retention & data map" },
};

export function OversightSection() {
  const q = useCellQuery([...PRIVACY_KEY, "oversight"], (api) => api.privacy.v2.oversight(), { refetchInterval: 60_000 });
  return (
    <QueryView query={q}>
      {(o) => (
        <div className="space-y-5">
          {o.attention.length ? (
            <Banner tone="warn" title={`${o.attention.length} thing(s) need attention`}>
              <ul className="list-disc pl-5" aria-label="Needs attention">
                {o.attention.map((a) => (
                  <li key={a}>
                    {ATTENTION[a]?.text ?? a} <span className="text-ink-muted">({ATTENTION[a]?.where ?? "Privacy"} tab)</span>
                  </li>
                ))}
              </ul>
            </Banner>
          ) : (
            <Banner tone="ok" icon={CheckCircle2} title="Nothing needs attention right now" />
          )}
          <Card title="Biometric controls" description={`Counts from your records. Activity figures cover the last ${o.window_days} days.`}>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Breaches pausing capture" value={o.breaches_pausing_capture} />
              <Stat label="DPIAs approved" value={o.dpia.approved} hint={`${o.dpia.expiring_30d} ending within 30 days · ${o.dpia.lapsed} lapsed`} />
              <Stat label="Models in service" value={o.models.active} hint={`${o.models.held} held`} />
              <Stat label="Asset classes without retention" value={o.retention_missing_classes.length} hint={o.retention_missing_classes.join(", ") || "All covered"} />
            </div>
          </Card>
          <Card title="People and their rights">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Reviews waiting" value={o.reviews.open} hint={`${o.reviews.overdue} overdue`} />
              <Stat label="Reversals waiting for a checker" value={o.reviews.awaiting_checker} />
              <Stat label="Consents withdrawn" value={o.consent.withdrawn_30d} hint={`last ${o.window_days} days`} />
              <Stat
                label="Erasure jobs open"
                value={o.erasure.open}
                hint={`${o.erasure.held} held · ${o.erasure.failed} failed · ${o.erasure.provider_tasks_overdue} provider deletions overdue`}
              />
            </div>
          </Card>
          <Card title="Suppliers">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Processor changes waiting" value={o.subprocessor_changes_waiting} />
              <Stat label="Transfers refused" value={o.transfers_refused_30d} hint={`last ${o.window_days} days`} />
            </div>
          </Card>
        </div>
      )}
    </QueryView>
  );
}
