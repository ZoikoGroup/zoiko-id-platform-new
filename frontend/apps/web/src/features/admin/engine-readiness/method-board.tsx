"use client";

import { Badge, Card, DataTable, LoadingRows, type Column } from "@zoiko/ui";

import { ApiErrorView, useCellQuery } from "@/lib/cell";

import { LEVEL_WORDS, methodName, ordered } from "./method-logic";

type Row = { method: string; level: keyof typeof LEVEL_WORDS; summary: string; needs: string[] };

const columns: Column<Row>[] = [
  { key: "m", header: "Method", pinned: true, render: (r) => methodName(r.method) },
  { key: "l", header: "Where it stands", render: (r) => <Badge tone={LEVEL_WORDS[r.level].tone}>{LEVEL_WORDS[r.level].label}</Badge> },
  { key: "s", header: "What exists", render: (r) => r.summary },
  { key: "n", header: "Still needed", render: (r) => (r.needs.length ? <ul className="list-disc pl-4">{r.needs.map((n) => <li key={n}>{n}</li>)}</ul> : "Nothing") },
];

/** P10: how far each identity-proofing method is from certified. Read only; it approves nothing. */
export function MethodBoard() {
  const q = useCellQuery(["verification", "method-certification"], (api) => api.verification.methodCertification());
  return (
    <Card title="Method certification" description="Built is not certified. A method is approved only when every record its approval needs is present.">
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {q.isError ? <ApiErrorView error={q.error} /> : null}
      {q.data ? (
        <div className="space-y-3 text-sm">
          <DataTable<Row> caption="Methods" rows={ordered(q.data.methods)} rowKey={(r) => r.method} empty="" columns={columns} />
          <p className="text-ink-muted">{q.data.note}</p>
        </div>
      ) : null}
    </Card>
  );
}
