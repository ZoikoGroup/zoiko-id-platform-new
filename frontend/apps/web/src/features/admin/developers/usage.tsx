"use client";

import type { UsageClient, UsageRow } from "@zoiko/api-client";
import { Card, DataTable, EmptyState, Select, type Column } from "@zoiko/ui";
import { Activity } from "lucide-react";
import { useMemo, useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

type SurfaceTotal = { surface: string; ok: number; client_errors: number; server_errors: number; total: number };

const DAYS = [
  { id: "1", label: "Today" },
  { id: "7", label: "Last 7 days" },
  { id: "30", label: "Last 30 days" },
];

function totals(rows: UsageRow[]): SurfaceTotal[] {
  const by = new Map<string, SurfaceTotal>();
  for (const r of rows) {
    const t = by.get(r.surface) ?? { surface: r.surface, ok: 0, client_errors: 0, server_errors: 0, total: 0 };
    if (r.status_class === "4xx") t.client_errors += r.calls;
    else if (r.status_class === "5xx") t.server_errors += r.calls;
    else t.ok += r.calls;
    t.total += r.calls;
    by.set(r.surface, t);
  }
  return [...by.values()].sort((a, b) => b.total - a.total);
}

function perDay(rows: UsageRow[]): { day: string; calls: number }[] {
  const by = new Map<string, number>();
  for (const r of rows) by.set(r.day, (by.get(r.day) ?? 0) + r.calls);
  return [...by.entries()].map(([day, calls]) => ({ day, calls }));
}

export function UsageSection() {
  const [days, setDays] = useState("7");
  const usage = useCellQuery(["admin", "usage", days], (api) => api.usage.summary(Number(days)), { refetchInterval: 30_000 });

  const surfaceColumns: Column<SurfaceTotal>[] = [
    { key: "surface", header: "API area", pinned: true, render: (s) => <span className="font-mono text-xs">{s.surface}</span>, sortValue: (s) => s.surface },
    { key: "total", header: "Calls", render: (s) => <span className="tabular">{s.total.toLocaleString()}</span>, sortValue: (s) => s.total },
    { key: "ok", header: "Succeeded", render: (s) => <span className="tabular">{s.ok.toLocaleString()}</span>, sortValue: (s) => s.ok },
    { key: "c", header: "Client errors (4xx)", render: (s) => <span className="tabular">{s.client_errors.toLocaleString()}</span>, sortValue: (s) => s.client_errors },
    { key: "s", header: "Server errors (5xx)", render: (s) => <span className="tabular">{s.server_errors.toLocaleString()}</span>, sortValue: (s) => s.server_errors },
    {
      key: "rate",
      header: "Error rate",
      render: (s) => `${s.total ? (((s.client_errors + s.server_errors) / s.total) * 100).toFixed(1) : "0.0"}%`,
      sortValue: (s) => (s.total ? (s.client_errors + s.server_errors) / s.total : 0),
    },
  ];
  const clientColumns: Column<UsageClient>[] = [
    { key: "client", header: "OAuth client", pinned: true, render: (c) => <span className="font-mono text-xs">{c.client_id}</span>, sortValue: (c) => c.client_id },
    { key: "calls", header: "Calls", render: (c) => <span className="tabular">{c.calls.toLocaleString()}</span>, sortValue: (c) => c.calls },
    { key: "errors", header: "Errors", render: (c) => <span className="tabular">{c.errors.toLocaleString()}</span>, sortValue: (c) => c.errors },
  ];
  const dayColumns: Column<{ day: string; calls: number }>[] = [
    { key: "day", header: "Day (UTC)", pinned: true, render: (d) => d.day, sortValue: (d) => d.day },
    { key: "calls", header: "Calls", render: (d) => <span className="tabular">{d.calls.toLocaleString()}</span>, sortValue: (d) => d.calls },
  ];

  return (
    <QueryView query={usage}>
      {(data) => <Body data={data} days={days} setDays={setDays} surfaceColumns={surfaceColumns} clientColumns={clientColumns} dayColumns={dayColumns} />}
    </QueryView>
  );
}

function Body({
  data,
  days,
  setDays,
  surfaceColumns,
  clientColumns,
  dayColumns,
}: {
  data: { items: UsageRow[]; clients: UsageClient[] };
  days: string;
  setDays: (v: string) => void;
  surfaceColumns: Column<SurfaceTotal>[];
  clientColumns: Column<UsageClient>[];
  dayColumns: Column<{ day: string; calls: number }>[];
}) {
  const surfaces = useMemo(() => totals(data.items), [data.items]);
  const daily = useMemo(() => perDay(data.items), [data.items]);
  const total = surfaces.reduce((n, s) => n + s.total, 0);
  const empty = (
    <EmptyState icon={Activity} title="No API calls yet">
      Calls made with an access token appear here within a few seconds. Counts only: paths with ids, query strings and request bodies are never recorded.
    </EmptyState>
  );
  return (
    <div className="space-y-4">
      <Card
        padded={false}
        title="API usage by area"
        description={`${total.toLocaleString()} authenticated call${total === 1 ? "" : "s"}. Counts by area and result, not what was asked or by whom.`}
        actions={<Select label="Period" options={DAYS} value={days} onChange={setDays} />}
      >
        <DataTable caption="API usage by area" columns={surfaceColumns} rows={surfaces} rowKey={(s) => s.surface} empty={empty} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card padded={false} title="By day">
          <DataTable caption="API calls per day" columns={dayColumns} rows={daily} rowKey={(d) => d.day} empty={empty} />
        </Card>
        <Card padded={false} title="By OAuth client">
          <DataTable caption="API calls per OAuth client" columns={clientColumns} rows={data.clients} rowKey={(c) => c.client_id} empty={empty} />
        </Card>
      </div>
    </div>
  );
}
