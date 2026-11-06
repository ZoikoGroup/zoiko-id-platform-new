"use client";

import type { ActivationRefund } from "@zoiko/api-client";
import { Banner, Button, Card, CopyId, DataTable, EmptyState, JsonView, TextField, type Column } from "@zoiko/ui";
import { Search, Undo2 } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

/** First-month plan payments marked to be returned. Finance makes the refund in Stripe with the reference shown; Stripe's report then closes the item. */
export function ActivationRefundsView() {
  const list = useCellQuery([...COMMERCIAL_KEY, "activation-refunds"], (api) => api.commercial.activationRefunds());
  const columns: Column<ActivationRefund>[] = [
    { key: "a", header: "Amount", pinned: true, render: (r) => `${r.amount} ${r.currency}`, sortValue: (r) => Number(r.amount) },
    { key: "p", header: "Stripe payment", render: (r) => (r.payment_ref ? <CopyId value={r.payment_ref} label="Stripe payment" /> : "—") },
    { key: "r", header: "Why", render: (r) => r.reason ?? "—" },
    { key: "o", header: "Order", render: (r) => <CopyId value={r.order_id} label="order" /> },
    { key: "t", header: "Company", render: (r) => <CopyId value={r.tenant_id} label="tenant" /> },
    { key: "d", header: "Marked", render: (r) => (r.decided_at ? new Date(r.decided_at).toLocaleString("en-GB") : "—"), sortValue: (r) => r.decided_at ?? "" },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card title="Plan payments to return" description="Refund each in Stripe using its payment reference. When Stripe reports the refund, the item closes by itself." padded={false}>
          <DataTable<ActivationRefund>
            caption="First-month plan payments waiting to be refunded"
            columns={columns}
            rows={d.items}
            rowKey={(r) => r.order_id}
            empty={<EmptyState title="Nothing to return" icon={Undo2}>A payment appears here when an order's first-month payment is marked to be returned.</EmptyState>}
          />
        </Card>
      )}
    </QueryView>
  );
}

/** What was deleted for a closed company, what a legal hold kept, and what is still owed. Asking for a deletion is the action above this card. */
export function DataDeletionStatusCard() {
  const [tenant, setTenant] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tenant.trim());
  const status = useCellQuery([...COMMERCIAL_KEY, "data-deletion", asked], (api) => api.commercial.dataDeletion(asked as string), { enabled: Boolean(asked) });
  return (
    <Card title="Data deletion status" description="Look up a closed company by its tenant ID.">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) setAsked(tenant.trim());
        }}
      >
        <TextField label="Tenant ID" value={tenant} onChange={setTenant} className="min-w-80" errorMessage={tenant && !valid ? "Enter the tenant UUID." : undefined} />
        <Button type="submit" variant="secondary" isDisabled={!valid} icon={<Search aria-hidden className="size-4" />}>
          Look up
        </Button>
      </form>
      {asked ? (
        <div className="mt-4">
          <QueryView query={status}>{(d) => <JsonView value={d} />}</QueryView>
        </div>
      ) : (
        <Banner tone="info" className="mt-4">Nothing is looked up until you ask.</Banner>
      )}
      {status.error ? <ApiErrorView error={status.error} title="Could not read the status" /> : null}
    </Card>
  );
}
