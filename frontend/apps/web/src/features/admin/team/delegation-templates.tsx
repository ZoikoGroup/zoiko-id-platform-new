"use client";

import type { DelegationTemplate } from "@zoiko/api-client";
import { Button, Card, DataTable, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FileStack, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["admin", "team", "delegation-templates"];

/** One item per line or comma, trimmed and de-duplicated. */
export function items(text: string): string[] {
  return [...new Set(text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean))];
}

function NewTemplate({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [actions, setActions] = useState("");
  const [resources, setResources] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("GBP");
  const [days, setDays] = useState("30");
  const hasValue = amount.trim() !== "";
  const valid =
    name.trim().length >= 2 && items(actions).length > 0 && items(resources).length > 0 && Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 365 && (!hasValue || (Number(amount) >= 0 && /^[A-Za-z]{3}$/.test(currency)));
  const create = useCellMutation(
    (api) =>
      api.delegationTemplates.create({
        name: name.trim(),
        actions: items(actions),
        resources: items(resources),
        value_amount: hasValue ? amount.trim() : null,
        value_currency: hasValue ? currency.toUpperCase() : null,
        max_days: Number(days),
      }),
    { invalidate: [KEY], success: "Template saved", onSuccess: () => close() },
  );
  function close() {
    setName("");
    setActions("");
    setResources("");
    setAmount("");
    create.reset();
    onClose();
  }
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !create.isPending && close()}
      title="New delegation template"
      description="A reusable shape for a delegation. Applying it issues an ordinary grant, so it can never carry more than the person applying it holds."
      footer={
        <>
          <Button variant="secondary" onPress={close} isDisabled={create.isPending}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Save template
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Supplier payments up to 5,000" />
        <TextField label="Actions" isRequired multiline rows={2} mono value={actions} onChange={setActions} placeholder="payment.release" description="One per line." />
        <TextField label="Resources" isRequired multiline rows={2} mono value={resources} onChange={setResources} placeholder="payment:*" description="One per line." />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Value limit (optional)" value={amount} onChange={setAmount} placeholder="5000" />
          <TextField label="Currency" value={currency} onChange={setCurrency} />
        </div>
        <TextField label="Lasts at most (days)" value={days} onChange={setDays} description="1 to 365." />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

export function DelegationTemplates() {
  const list = useCellQuery(KEY, (api) => api.delegationTemplates.list());
  const [creating, setCreating] = useState(false);
  const retire = useCellMutation((api, id: string) => api.delegationTemplates.retire(id), { invalidate: [KEY], success: "Template retired" });
  const columns: Column<DelegationTemplate>[] = [
    { key: "name", header: "Template", pinned: true, render: (t) => <span className="font-medium">{t.name}</span>, sortValue: (t) => t.name },
    { key: "actions", header: "Actions", render: (t) => <span className="font-mono text-xs">{t.actions.join(", ")}</span> },
    { key: "limit", header: "Limit", render: (t) => (t.value_limit ? `${t.value_limit.amount} ${t.value_limit.currency}` : "None") },
    { key: "days", header: "Lasts", align: "end", render: (t) => `${t.max_days} days`, sortValue: (t) => t.max_days },
    { key: "status", header: "Status", render: (t) => <StatusBadge state={t.status} kind="Template" /> },
    {
      key: "do",
      header: "Actions",
      align: "end",
      render: (t) =>
        t.status === "ACTIVE" ? (
          <Button size="xs" variant="secondary" isDisabled={retire.isPending} onPress={() => retire.mutate(t.template_id)}>
            Retire
          </Button>
        ) : null,
    },
  ];
  return (
    <>
      <Card
        title="Delegation templates"
        description="Reusable shapes for the authority you hand out. Part of the delegation workflow templates plan feature."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            New template
          </Button>
        }
        padded={false}
      >
        <QueryView query={list}>
          {(d) => (
            <DataTable
              caption="Delegation templates"
              columns={columns}
              rows={d.items}
              rowKey={(t) => t.template_id}
              empty={
                <EmptyState icon={FileStack} title="No templates yet">
                  Save the delegations you give often so each one is issued the same way.
                </EmptyState>
              }
            />
          )}
        </QueryView>
        {retire.error ? (
          <div className="p-4">
            <ApiErrorView error={retire.error} />
          </div>
        ) : null}
      </Card>
      <NewTemplate isOpen={creating} onClose={() => setCreating(false)} />
    </>
  );
}
