"use client";

import type { AssuranceControl } from "@zoiko/api-client";
import { Banner, Card, Select } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

const WINDOWS = [
  { id: "7", label: "Last 7 days" },
  { id: "30", label: "Last 30 days" },
  { id: "90", label: "Last 90 days" },
];

export function percent(rate: number | null): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(rate === 1 || rate === 0 ? 0 : 1)}%`;
}

/** What a control's counts say in one line, for the report. */
export function controlLine(c: AssuranceControl): string {
  if (c.of === 0) return "No decisions of this kind in the window.";
  return `${c.passed} of ${c.of} (${percent(c.rate)})`;
}

function Bar({ value, of }: { value: number; of: number }) {
  const w = of ? Math.max(2, Math.round((value / of) * 100)) : 0;
  return (
    <span className="block h-2 w-full rounded-full bg-surface-sunken" aria-hidden>
      <span className="block h-2 rounded-full bg-brand-600" style={{ width: `${w}%` }} />
    </span>
  );
}

function Risk({ days, unit }: { days: number; unit: string }) {
  const q = useCellQuery(["admin", "insights", "risk", days, unit], (api) => api.insights.risk(days, unit || undefined));
  return (
    <QueryView query={q}>
      {(d) => (
        <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-subtle">Decisions</dt>
              <dd className="text-xl font-semibold text-ink">{d.total}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-subtle">Refused</dt>
              <dd className="text-xl font-semibold text-ink">{percent(d.refusal_rate)}</dd>
            </div>
            {Object.entries(d.by_outcome)
              .filter(([k]) => k !== "ALLOW")
              .slice(0, 2)
              .map(([k, n]) => (
                <div key={k}>
                  <dt className="text-xs text-ink-subtle">{k.replace(/_/g, " ").toLowerCase()}</dt>
                  <dd className="text-xl font-semibold text-ink">{n}</dd>
                </div>
              ))}
          </dl>
          <div className="grid gap-6 lg:grid-cols-2">
            <section aria-label="Top refusal reasons">
              <h4 className="mb-2 text-sm font-medium text-ink">Why decisions were refused</h4>
              {d.top_refusal_reasons.length ? (
                <ul className="space-y-2">
                  {d.top_refusal_reasons.map((r) => (
                    <li key={r.code} className="space-y-1">
                      <span className="flex justify-between text-xs">
                        <span className="font-mono">{r.code}</span>
                        <span>{r.count}</span>
                      </span>
                      <Bar value={r.count} of={d.top_refusal_reasons[0]?.count ?? 1} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-muted">Nothing was refused in this window.</p>
              )}
            </section>
            <section aria-label="Busiest actions">
              <h4 className="mb-2 text-sm font-medium text-ink">Busiest actions</h4>
              <ul className="space-y-2">
                {d.top_actions.map((a) => (
                  <li key={a.action} className="space-y-1">
                    <span className="flex justify-between text-xs">
                      <span className="font-mono">{a.action}</span>
                      <span>
                        {a.count} ({a.refused} refused)
                      </span>
                    </span>
                    <Bar value={a.count} of={d.top_actions[0]?.count ?? 1} />
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      )}
    </QueryView>
  );
}

function Assurance({ days, unit }: { days: number; unit: string }) {
  const q = useCellQuery(["admin", "insights", "assurance", days, unit], (api) => api.insights.assurance(days, unit || undefined));
  return (
    <QueryView query={q}>
      {(d) => (
        <div className="space-y-3">
          {d.controls.map((c) => (
            <div key={c.control} className="flex items-start justify-between gap-4 rounded-md border border-border p-3">
              <div>
                <p className="text-sm font-medium text-ink">{c.label}</p>
                <p className="text-xs text-ink-muted">{controlLine(c)}</p>
              </div>
              <span className={c.met === false ? "text-sm font-medium text-danger-text" : "text-sm font-medium text-ok-text"}>{c.met === null ? "No data" : c.met ? "Met" : "Not met"}</span>
            </div>
          ))}
          <p className="text-xs text-ink-subtle">
            {d.decisions} decision records · {d.state_unavailable} could not be decided because a state was unavailable · {d.policy_releases_in_force.length} policy release(s) in force
          </p>
        </div>
      )}
    </QueryView>
  );
}

export function Insights() {
  const [days, setDays] = useState("30");
  const [unit, setUnit] = useState("");
  const units = useCellQuery(["admin", "team", "org-units"], (api) => api.tenant.orgUnits());
  const unitOptions = [{ id: "", label: "Whole company" }, ...(units.data?.items ?? []).map((u) => ({ id: u.unit_id, label: `${"— ".repeat(u.depth)}${u.name}` }))];
  return (
    <div className="mt-6 space-y-4">
      <Banner tone="info" title="Counted from your decision records">
        These figures count append-only decision records. They show codes and action names, never people or resources. Risk dashboards and assurance reporting are plan features.
      </Banner>
      <Card title="Risk" description="Where decisions were refused, and why." actions={
          <span className="flex gap-2">
            {unitOptions.length > 1 ? <Select label="Unit" value={unit} onChange={setUnit} options={unitOptions} /> : null}
            <Select label="Window" value={days} onChange={setDays} options={WINDOWS} />
          </span>
        }>
        <Risk days={Number(days)} unit={unit} />
      </Card>
      <Card title="Assurance" description="Controls the decision records evidence for the same window.">
        <Assurance days={Number(days)} unit={unit} />
      </Card>
    </div>
  );
}
