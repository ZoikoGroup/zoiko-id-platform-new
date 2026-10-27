"use client";

import type { BillPreview } from "@zoiko/api-client";
import { Banner, Card, EmptyState, KeyValues } from "@zoiko/ui";
import { Receipt } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, meterLabel, money } from "./shared";

export function PreviewSection() {
  const preview = useCellQuery([...BILLING_KEY, "preview"], (api) => api.billing.preview(), { refetchInterval: 60_000 });
  return <QueryView query={preview}>{(p) => <Body p={p} />}</QueryView>;
}

function Body({ p }: { p: BillPreview }) {
  if (p.status === "UNPRICED") {
    return (
      <EmptyState title="Not priced yet" icon={Receipt}>
        No approved price book covers this month, so there is no bill to preview. Nothing is charged until prices are approved.
      </EmptyState>
    );
  }
  const cur = p.currency;
  return (
    <div className="space-y-4">
      <Card title={`Bill so far in ${p.month}`} description="An estimate that uses the same arithmetic as your invoice. It is not a quotation or an invoice.">
        <KeyValues
          columns={3}
          items={[
            ["Bill so far", <strong key="a">{money(p.bill_to_date, cur)}</strong>],
            [
              "Projected month-end bill",
              p.projected_bill ? money(p.projected_bill, cur) : "Not enough of the month has passed to project",
            ],
            ["Projection confidence", p.projection_confidence ?? "—"],
          ]}
        />
        {p.warnings?.length ? (
          <Banner tone="info" className="mt-3">
            {p.warnings.join(" ")}
          </Banner>
        ) : null}
      </Card>
      <Card title="How it adds up" padded={false}>
        <table className="w-full text-sm">
          <caption className="sr-only">Bill lines to date with their formulas</caption>
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
              <th scope="col" className="px-4 py-2">
                Line
              </th>
              <th scope="col" className="px-4 py-2">
                Formula
              </th>
              <th scope="col" className="px-4 py-2 text-right">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {(p.lines_to_date ?? []).map((ln, i) => (
              <tr key={i} className="border-b border-border last:border-0">
                <th scope="row" className="px-4 py-2 text-left font-medium">
                  {ln.kind === "FEE" ? "Platform fee" : meterLabel(ln.meter)}
                </th>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{ln.formula}</td>
                <td className="tabular px-4 py-2 text-right">{money(ln.net, cur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <ul className="list-disc space-y-1 pl-5 text-xs text-ink-muted">
        {p.assumptions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </div>
  );
}
