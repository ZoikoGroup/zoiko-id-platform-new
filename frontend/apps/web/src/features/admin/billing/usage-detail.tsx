"use client";

import type { BillableEvent, RatedLine } from "@zoiko/api-client";
import { Badge, Card, DataTable, EmptyState, Select, type Column } from "@zoiko/ui";
import { Calculator, ListChecks } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, METER_LABEL, count, meterLabel, money } from "./shared";

/** The rated lines of a month, each with the arithmetic that produced it: a customer can check the bill. */
export function RatedLines({ month }: { month: string }) {
  const rated = useCellQuery([...BILLING_KEY, "rated", month], (api) => api.billing.rated(month));
  const columns: Column<RatedLine>[] = [
    { key: "what", header: "Charge", pinned: true, render: (l) => <span className="font-medium">{l.meter ? meterLabel(l.meter) : l.kind}</span> },
    { key: "kind", header: "Kind", render: (l) => <Badge tone="neutral">{l.kind.toLowerCase().replaceAll("_", " ")}</Badge> },
    { key: "formula", header: "How it was calculated", render: (l) => <code className="text-xs">{l.formula}</code> },
    { key: "net", header: "Amount", render: (l) => <span className="tabular">{money(l.net, rated.data?.currency)}</span>, sortValue: (l) => Number(l.net) },
  ];
  return (
    <QueryView
      query={rated}
      loadingRows={2}
      notFound={
        <Card title="How your bill is calculated" description="Each line comes with its formula.">
          <EmptyState title="This month has not been rated yet" icon={Calculator}>
            Rating runs when the month closes. Until then the bill preview shows an estimate.
          </EmptyState>
        </Card>
      }
    >
      {(r) => (
        <Card
          title={`How your ${r.period.slice(0, 7)} bill is calculated`}
          description="Every line carries the formula that produced it, so the total can be reproduced from the usage events below."
          padded={false}
        >
          <DataTable<RatedLine>
            caption={`Rated lines for ${r.period.slice(0, 7)}`}
            columns={columns}
            rows={r.lines}
            rowKey={(l) => [l.kind, l.sku, l.meter, l.plan, l.segment, l.formula, l.net].join("|")}
            empty={<EmptyState title="No charges" icon={Calculator}>Nothing was charged for this month.</EmptyState>}
          />
          <p className="border-t border-border px-4 py-3 text-sm">
            <span className="text-ink-muted">Subtotal </span>
            <span className="tabular font-semibold">{money(r.subtotal, r.currency)}</span>
            {Number(r.credit_balance) !== 0 ? <span className="ml-3 text-ink-muted">Credit available {money(r.credit_balance, r.currency)}</span> : null}
          </p>
          {r.warnings.length ? <p className="px-4 pb-3 text-xs text-warn-fg">{r.warnings.join(" ")}</p> : null}
        </Card>
      )}
    </QueryView>
  );
}

const METER_OPTIONS = [{ id: "", label: "All meters" }, ...Object.entries(METER_LABEL).map(([id, label]) => ({ id, label }))];

/** The events behind a meter. Truncation is stated, never hidden. */
export function MeterEvents({ month }: { month: string }) {
  const [meter, setMeter] = useState("");
  const events = useCellQuery([...BILLING_KEY, "events", month, meter], (api) => api.billing.billableEvents(month, meter || undefined, 200));
  const columns: Column<BillableEvent>[] = [
    { key: "at", header: "When (UTC)", pinned: true, render: (e) => new Date(e.occurred_at).toISOString().replace("T", " ").slice(0, 19), sortValue: (e) => e.occurred_at },
    { key: "meter", header: "Meter", render: (e) => meterLabel(e.meter), sortValue: (e) => e.meter },
    { key: "qty", header: "Quantity", render: (e) => <span className="tabular">{count(e.quantity)}</span>, sortValue: (e) => Number(e.quantity) },
    {
      key: "flags",
      header: "Note",
      render: (e) => (
        <span className="flex gap-1">
          {e.late ? <Badge tone="warn">arrived late</Badge> : null}
          {e.recovered ? <Badge tone="info">recovered</Badge> : null}
        </span>
      ),
    },
    { key: "id", header: "Event", render: (e) => <span className="font-mono text-xs">{e.event_id.slice(0, 13)}</span> },
  ];
  return (
    <Card
      title="Events behind the meters"
      description="Each chargeable operation is one event with a stable id. Use these to reproduce any number above."
      padded={false}
    >
      <div className="p-4">
        <Select label="Meter" options={METER_OPTIONS} value={meter} onChange={setMeter} className="max-w-xs" />
      </div>
      <QueryView query={events} loadingRows={3}>
        {(data) => (
          <>
            <DataTable<BillableEvent>
              caption="Billable events"
              columns={columns}
              rows={data.items}
              rowKey={(e) => e.event_id}
              empty={<EmptyState title="No events" icon={ListChecks}>Nothing chargeable was recorded for this selection.</EmptyState>}
            />
            {data.truncated ? (
              <p className="border-t border-border px-4 py-3 text-xs text-ink-subtle">
                Showing the first {data.items.length} events. Download the ledger (CSV) for every event.
              </p>
            ) : null}
          </>
        )}
      </QueryView>
    </Card>
  );
}
