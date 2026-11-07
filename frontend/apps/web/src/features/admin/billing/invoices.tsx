"use client";

import type { Invoice } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, Select, SidePanel, TextField, type Column, type Tone } from "@zoiko/ui";
import { FileText } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AsOf } from "./as-of";
import { INVOICE_STATE_LABEL, NO_FILTER, filterInvoices, invoiceState, isPayable, type InvoiceFilter, type InvoiceState } from "./invoice-logic";
import { BILLING_KEY, money } from "./shared";

const STATE_TONE: Record<InvoiceState, Tone> = { DRAFT: "neutral", OPEN: "info", PARTIAL: "warn", OVERDUE: "danger", PAID: "ok", CREDITED: "ok", VOID: "neutral", PENDING_VERIFICATION: "warn" };

function StateBadge({ invoice }: { invoice: Invoice }) {
  const s = invoiceState(invoice);
  return <Badge tone={STATE_TONE[s]}>{INVOICE_STATE_LABEL[s]}</Badge>;
}

export function InvoicesSection() {
  const invoices = useCellQuery([...BILLING_KEY, "invoices"], (api) => api.billing.invoices());
  const [open, setOpen] = useState<Invoice | null>(null);
  const [filter, setFilter] = useState<InvoiceFilter>(NO_FILTER);
  const set = (patch: Partial<InvoiceFilter>) => setFilter((f) => ({ ...f, ...patch }));
  const currencies = useMemo(() => [...new Set((invoices.data?.items ?? []).map((i) => i.currency))].sort(), [invoices.data]);

  const columns: Column<Invoice>[] = [
    { key: "number", header: "Invoice", pinned: true, render: (i) => <span className="font-mono text-xs">{i.number ?? "—"}</span>, sortValue: (i) => i.number ?? "" },
    { key: "period", header: "Period", render: (i) => i.period, sortValue: (i) => i.period },
    { key: "gross", header: "Total", render: (i) => money(i.gross, i.currency), sortValue: (i) => Number(i.gross) },
    { key: "state", header: "Status", render: (i) => <StateBadge invoice={i} />, sortValue: (i) => invoiceState(i) },
    {
      key: "outstanding",
      header: "Balance",
      render: (i) => (i.outstanding === null ? "—" : money(i.outstanding, i.currency)),
      sortValue: (i) => Number(i.outstanding ?? 0),
    },
    { key: "due", header: "Due", render: (i) => (i.due_at ? new Date(i.due_at).toLocaleDateString("en-GB") : "—"), sortValue: (i) => i.due_at ?? "" },
  ];

  return (
    <>
      <QueryView query={invoices}>
        {(data) => {
          const rows = filterInvoices(data.items, filter);
          const filtered = JSON.stringify(filter) !== JSON.stringify(NO_FILTER);
          return (
            <Card
              title="Invoices and credit notes"
              description="Issued invoices are immutable. A correction is a linked credit note, never an edit."
              padded={false}
            >
              <div className="grid gap-3 border-b border-border px-5 py-3 sm:grid-cols-2 lg:grid-cols-5">
                <TextField label="Search" value={filter.text} onChange={(v) => set({ text: v })} placeholder="Invoice number or period" />
                <Select
                  label="Status"
                  value={filter.state}
                  onChange={(v) => set({ state: v })}
                  options={[{ id: "", label: "Any status" }, ...Object.entries(INVOICE_STATE_LABEL).map(([id, label]) => ({ id, label }))]}
                />
                <Select label="Currency" value={filter.currency} onChange={(v) => set({ currency: v })} options={[{ id: "", label: "Any currency" }, ...currencies.map((c) => ({ id: c, label: c }))]} />
                <TextField label="Issued from" type="date" value={filter.from} onChange={(v) => set({ from: v })} />
                <TextField label="Issued to" type="date" value={filter.to} onChange={(v) => set({ to: v })} />
              </div>
              <div className="flex items-center justify-between px-5 py-2">
                <AsOf at={invoices.dataUpdatedAt} />
                {filtered ? (
                  <Button size="xs" variant="secondary" onPress={() => setFilter(NO_FILTER)}>
                    Clear filters
                  </Button>
                ) : null}
              </div>
              <div className="hidden md:block">
                <DataTable<Invoice>
                  caption="Issued invoices"
                  columns={columns}
                  rows={rows}
                  rowKey={(i) => i.invoice_id}
                  onRowAction={(i) => setOpen(i)}
                  empty={
                    <EmptyState title={filtered ? "No invoice matches these filters" : "No invoices yet"} icon={FileText}>
                      {filtered ? "Clear a filter to see more." : "Invoices appear here once a month has been closed and issued."}
                    </EmptyState>
                  }
                />
              </div>
              <ul className="divide-y divide-border md:hidden" aria-label="Issued invoices">
                {rows.length === 0 ? (
                  <li className="px-5 py-6 text-sm text-ink-muted">{filtered ? "No invoice matches these filters." : "No invoices yet."}</li>
                ) : (
                  rows.map((i) => (
                    <li key={i.invoice_id}>
                      <button type="button" className="flex w-full flex-col gap-1 px-5 py-3 text-left" onClick={() => setOpen(i)}>
                        <span className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs">{i.number ?? "—"}</span>
                          <StateBadge invoice={i} />
                        </span>
                        <span className="text-sm">{money(i.gross, i.currency)}</span>
                        <span className="text-xs text-ink-muted">
                          {i.period} · due {i.due_at ? new Date(i.due_at).toLocaleDateString("en-GB") : "—"}
                        </span>
                      </button>
                    </li>
                  ))
                )}
              </ul>
            </Card>
          );
        }}
      </QueryView>
      {open ? <Detail invoice={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function Detail({ invoice: i, onClose }: { invoice: Invoice; onClose: () => void }) {
  const payment = useCellQuery([...BILLING_KEY, "payment"], (api) => api.billing.payment());
  const online = useCellMutation((api, id: string) => api.billing.payOnline(id), { onSuccess: (r) => window.location.assign(r.url) });
  const canPay = payment.data?.can_manage === true && isPayable(i);
  const state = invoiceState(i);
  return (
    <SidePanel title={`Invoice ${i.number ?? ""}`} isOpen onOpenChange={(o) => !o && onClose()}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StateBadge invoice={i} />
        {state === "PENDING_VERIFICATION" ? <span className="text-xs text-ink-muted">The balance is being confirmed. Payment is closed until it is.</span> : null}
      </div>
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
      {i.credit_notes?.length ? (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-medium text-ink">Credit notes against this invoice</h3>
          <table className="w-full text-sm">
            <caption className="sr-only">Credit notes</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
                <th scope="col" className="py-2">Credit note</th>
                <th scope="col" className="py-2">Reason</th>
                <th scope="col" className="py-2 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {i.credit_notes.map((c) => (
                <tr key={c.number} className="border-b border-border last:border-0">
                  <th scope="row" className="py-2 text-left font-mono text-xs font-normal">{c.number}</th>
                  <td className="py-2">{c.reason_code.replace(/_/g, " ").toLowerCase()} · {new Date(c.issued_at).toLocaleDateString("en-GB")}</td>
                  <td className="tabular py-2 text-right">{money(c.amount, i.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {canPay ? (
        <div className="mt-4 space-y-2">
          <Button pending={online.isPending} onPress={() => online.mutate(i.invoice_id)}>
            Pay {money(i.outstanding, i.currency)} on Stripe
          </Button>
          {online.isError ? <ApiErrorView error={online.error} title="Couldn't open the payment page" onRetry={() => online.reset()} /> : null}
        </div>
      ) : isPayable(i) ? (
        <Banner tone="info" className="mt-4">
          Only the company owner, or someone the owner gave payment rights, can pay this invoice.
        </Banner>
      ) : null}
      {state === "PARTIAL" || state === "OVERDUE" || state === "PENDING_VERIFICATION" ? (
        <p className="mt-3 text-sm">
          Think this is wrong?{" "}
          <a className="font-medium text-brand-700 underline" href={`/help?topic=billing-dispute&invoice=${encodeURIComponent(i.number ?? i.invoice_id)}`}>
            Ask billing support about this invoice
          </a>
          .
        </p>
      ) : null}
      <p className="mt-4 text-sm">
        <a className="font-medium text-brand-700 underline" href={`/api/bff/v1/invoices/${i.invoice_id}/document`} download>
          Download the invoice document
        </a>
      </p>
      {i.document_digest ? <p className="mt-1 break-all font-mono text-2xs text-ink-subtle">SHA-256 {i.document_digest}</p> : null}
    </SidePanel>
  );
}
