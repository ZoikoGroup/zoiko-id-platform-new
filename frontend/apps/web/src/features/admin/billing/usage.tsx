"use client";

import type { BillableMeter, EntitlementView } from "@zoiko/api-client";
import { Banner, Card, EmptyState, Meter, Select } from "@zoiko/ui";
import { Gauge } from "lucide-react";
import { useState } from "react";

import { useTenant } from "@/features/onboarding/shared";
import { QueryView, useCellQuery } from "@/lib/cell";

import { ALLOWANCE_FIELD, BILLING_KEY, count, meterLabel, usageTone } from "./shared";
import { MeterEvents, RatedLines } from "./usage-detail";

function lastMonths(n: number): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  const d = new Date();
  d.setUTCDate(1);
  for (let i = 0; i < n; i++) {
    const id = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    out.push({ id, label: i === 0 ? `${id} (this month)` : id });
    d.setUTCMonth(d.getUTCMonth() - 1);
  }
  return out;
}

export function UsageSection() {
  const months = lastMonths(6);
  const [month, setMonth] = useState(months[0]!.id);
  const tenant = useTenant();
  const id = tenant.data?.tenant_id;
  const ledger = useCellQuery([...BILLING_KEY, "ledger", month], (api) => api.billing.ledger(month));
  const plan = useCellQuery([...BILLING_KEY, "entitlements", id], (api) => api.billing.entitlements(id as string), { enabled: Boolean(id) });

  return (
    <div className="space-y-4">
      <Select label="Month (UTC)" options={months} value={month} onChange={setMonth} className="max-w-xs" />
      <QueryView query={ledger}>
        {(l) => {
          const rows = l.meters.filter((m) => Number(m.net) > 0 || m.events > 0 || m.meter in ALLOWANCE_FIELD);
          return (
            <Card
              title={`Usage in ${l.month.slice(0, 7)}`}
              description="Each chargeable operation is counted once. Warnings appear at 50%, 80% and 100% of what your plan includes; using more than that is billed, never blocked."
              actions={
                <a className="text-sm font-medium text-brand-700 underline" href={`/api/bff/v1/usage/billable/export?month=${month}`}>
                  Download ledger (CSV)
                </a>
              }
            >
              {rows.length === 0 ? (
                <EmptyState title="No usage recorded" icon={Gauge}>Nothing chargeable happened in this month.</EmptyState>
              ) : (
                <div className="space-y-4">
                  {plan.isError ? <Banner tone="warn">Your plan allowances could not be read, so only counts are shown.</Banner> : null}
                  {rows.map((m) => (
                    <MeterRow key={m.meter} m={m} limits={plan.data?.limits} />
                  ))}
                </div>
              )}
            </Card>
          );
        }}
      </QueryView>
      <RatedLines month={month} />
      <MeterEvents month={month} />
    </div>
  );
}

function MeterRow({ m, limits }: { m: BillableMeter; limits: EntitlementView["limits"] | undefined }) {
  const net = Number(m.net);
  const field = ALLOWANCE_FIELD[m.meter as keyof typeof ALLOWANCE_FIELD];
  const included = field && limits ? limits[field] : null;
  const label = meterLabel(m.meter);
  if (included === null || included === undefined) {
    return (
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium text-ink-muted">{label}</span>
        <span className="tabular font-semibold">
          {count(net)} <span className="text-xs font-normal text-ink-subtle">{field ? "(allowance negotiated)" : "(metered from the first)"}</span>
        </span>
      </div>
    );
  }
  const pct = included > 0 ? (net / included) * 100 : 100;
  return (
    <div>
      <Meter label={label} value={net} max={Math.max(included, 1)} tone={usageTone(pct)} valueText={`${count(net)} of ${count(included)} (${Math.round(pct)}%)`} />
      {Number(m.adjustments) !== 0 ? <p className="mt-1 text-xs text-ink-subtle">Includes an approved adjustment of {count(m.adjustments)}.</p> : null}
      {pct > 100 ? <p className="mt-1 text-xs text-warn-fg">{count(net - included)} over the included amount; the overage is billed at your plan rate.</p> : null}
    </div>
  );
}
