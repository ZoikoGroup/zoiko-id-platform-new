"use client";

import type { Invoice } from "@zoiko/api-client";
import { Badge, Card, DataTable, EmptyState, KeyValues, SidePanel, type Column } from "@zoiko/ui";
import { FileText } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, money } from "./shared";

export function InvoicesSection() {
  const invoices = useCellQuery([...BILLING_KEY, "invoices"], (api) => api.billing.invoices());
  const [open, setOpen] = useState<Invoice | null>(null);

  const columns: Column<Invoice>[] = [
    { key: "number", header: "Invoice", pinned: true, render: (i) => <span className="font-mono text-xs">{i.number ?? "—"}</span>, sortValue: (i) => i.number ?? "" },
    { key: "period", header: "Period", render: (i) => i.period, sortValue: (i) => i.period },
    { key: "gross", header: "Total", render: (i) => money(i.gross, i.currency), sortValue: (i) => Number(i.gross) },
    {
      key: "outstanding",
      header: "Outstanding",
      render: (i) => (Number(i.outstanding ?? 0) > 0 ? <Badge tone="warn">{money(i.outstanding, i.currency)}</Badge> : <Badge tone="ok">Settled</Badge>),
      sortValue: (i) => Number(i.outstanding ?? 0),
    },
    { key: "due", header: "Due", render: (i) => (i.due_at ? new Date(i.due_at).toLocaleDateString("en-GB") : "—"), sortValue: (i) => i.due_at ?? "" },
  ];

  return (
    <>
      <QueryView query={invoices}>
        {(data) => (
          <Card
            title="Invoices and credit notes"
            description="Issued invoices are immutable. A correction is a linked credit note, never an edit."
            padded={false}
          >
            <DataTable<Invoice>
              caption="Issued invoices"
              columns={columns}
              rows={data.items}
              rowKey={(i) => i.invoice_id}
              onRowAction={(i) => setOpen(i)}
              empty={<EmptyState title="No invoices yet" icon={FileText}>Invoices appear here once a month has been closed and issued.</EmptyState>}
            />
          </Card>
        )}
      </QueryView>
      {open ? <Detail invoice={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function Detail({ invoice: i, onClose }: { invoice: Invoice; onClose: () => void }) {
  return (
    <SidePanel title={`Invoice ${i.number ?? ""}`} isOpen onOpenChange={(o) => !o && onClose()}>
      <KeyValues
        columns={2}
        items={[
          ["Period", i.period],
          ["Issued", i.issued_at ? new Date(i.issued_at).toLocaleDateString("en-GB") : "—"],
          ["Subtotal", money(i.subtotal, i.currency)],
          ["Tax", i.tax_status === "NOT_COMPUTED" ? "Not computed" : money(i.tax_total, i.currency)],
          ["Total", money(i.gross, i.currency)],
          ["Credited", money(i.credited, i.currency)],
          ["Paid", money(i.paid, i.currency)],
          ["Outstanding", money(i.outstanding, i.currency)],
        ]}
      />
      <table className="mt-4 w-full text-sm">
        <caption className="sr-only">Invoice lines</caption>
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
            <th scope="col" className="py-2">
              Description
            </th>
            <th scope="col" className="py-2 text-right">
              Net
            </th>
          </tr>
        </thead>
        <tbody>
          {i.lines.map((ln) => (
            <tr key={ln.seq} className="border-b border-border last:border-0">
              <th scope="row" className="py-2 text-left font-normal">
                {ln.description}
              </th>
              <td className="tabular py-2 text-right">{money(ln.net, i.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-4 text-sm">
        <a className="font-medium text-brand-700 underline" href={`/api/bff/v1/invoices/${i.invoice_id}/document`} download>
          Download the invoice document
        </a>
      </p>
      {i.document_digest ? <p className="mt-1 break-all font-mono text-2xs text-ink-subtle">SHA-256 {i.document_digest}</p> : null}
    </SidePanel>
  );
}
