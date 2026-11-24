"use client";

import type { VerifyApiUsageRow } from "@zoiko/api-client";
import { Badge, Banner, Card, DataTable, EmptyState, Select, type Column } from "@zoiko/ui";
import { BarChart3 } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { COUNT_LABELS, hasAny, monthLabel, reconciliationText, recentMonths } from "./usage-logic";

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
              <Banner tone="info" title="Counts only">
                {d.billing.note}
              </Banner>
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
