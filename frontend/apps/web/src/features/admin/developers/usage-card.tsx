"use client";

import type { VerifyApiCost, VerifyApiUsageRow } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, Select, type Column } from "@zoiko/ui";
import { BarChart3 } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { COUNT_LABELS, costHeading, hasAny, money, monthLabel, reconciliationText, recentMonths } from "./usage-logic";

/** What your applications used this month (CR-154): sessions started and how they ended, with tests in the sandbox shown apart from live use. Counts only; nothing is priced yet. */
export function UsageCard() {
  const months = recentMonths(new Date());
  const [month, setMonth] = useState(months[0]!);
  const q = useCellQuery(["verify-api", "usage", month], (api) => api.verifyApi.usage(month));
  const columns: Column<VerifyApiUsageRow>[] = [
    { key: "a", header: "Application", pinned: true, render: (r) => <span className="font-medium">{r.application_name}</span> },
    { key: "e", header: "Where", render: (r) => <Badge tone={r.environment === "sandbox" ? "info" : "warn"}>{r.environment === "sandbox" ? "Sandbox test" : "Live"}</Badge> },
    ...COUNT_LABELS.map((c) => ({ key: c.key, header: c.label, align: "end" as const, render: (r: VerifyApiUsageRow) => r[c.key] })),
  ];
  return (
    <Card
      title="Usage"
      description="Verifications started and how they ended. Sandbox tests are shown apart and are never counted as usage."
      actions={<Select label="Month" hideLabel options={months.map((m) => ({ id: m, label: monthLabel(m) }))} value={month} onChange={setMonth} />}
      padded={false}
    >
      <QueryView query={q}>
        {(d) => {
          const rec = reconciliationText(d.reconciliation);
          return (
            <div className="space-y-3 p-4">
              <Banner tone="info" title={d.billing.state === "INDICATIVE_COST" ? "Indicative cost" : "Counts only"}>
                {d.billing.note}
              </Banner>
              {d.billing.cost.map((c) => (
                <CostBlock key={c.currency} cost={c} />
              ))}
              {hasAny(d.totals.production) ? <Banner tone={rec.ok ? "ok" : "warn"} title="Usage records">{rec.text}</Banner> : null}
              <DataTable<VerifyApiUsageRow>
                caption={`Usage for ${monthLabel(d.month)}`}
                columns={columns}
                rows={d.items}
                rowKey={(r) => `${r.application_id}-${r.environment}`}
                empty={
                  <EmptyState title="Nothing this month" icon={BarChart3} compact>
                    Verifications you start will be counted here.
                  </EmptyState>
                }
              />
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}

/** One currency's indicative cost: each usage fact with the price it used, then the total. An incomplete total says so instead of looking like the whole amount. */
function CostBlock({ cost }: { cost: VerifyApiCost }) {
  return (
    <section aria-label={`Indicative cost in ${cost.currency}`} className="space-y-2 rounded-lg border border-border p-3">
      <h3 className="text-sm font-semibold text-ink">{costHeading(cost)}</h3>
      <ul className="space-y-1 text-sm">
        {cost.lines.map((l) => (
          <li key={l.meter} className="flex flex-wrap justify-between gap-2">
            <span>
              {l.label}: {l.units} x {money(l.unit_rate, cost.currency)} <span className="text-xs text-ink-muted">(price from {l.price_from})</span>
            </span>
            <span className="font-mono">{money(l.amount, cost.currency)}</span>
          </li>
        ))}
      </ul>
      <p className="flex justify-between gap-2 border-t border-border pt-2 text-sm font-medium">
        <span>{cost.complete ? "Total" : "Total so far"}</span>
        <span className="font-mono">{money(cost.total, cost.currency)}</span>
      </p>
      {cost.complete ? null : <p className="text-xs text-ink-muted">Some usage in this month has no approved price in {cost.currency} yet, so this is not the full amount.</p>}
    </section>
  );
}
