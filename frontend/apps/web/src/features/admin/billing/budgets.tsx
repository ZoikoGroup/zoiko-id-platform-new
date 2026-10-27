"use client";

import type { UsageBudget } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { PiggyBank } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, METER_LABEL, count, meterLabel, money } from "./shared";

const TARGETS = [{ id: "", label: "The whole monthly bill" }, ...Object.entries(METER_LABEL).map(([id, label]) => ({ id, label }))];

export function BudgetsSection() {
  const budgets = useCellQuery([...BILLING_KEY, "budgets"], (api) => api.billing.budgets());
  const preview = useCellQuery([...BILLING_KEY, "preview"], (api) => api.billing.preview());
  const currency = preview.data?.currency ?? "USD";

  const [target, setTarget] = useState("");
  const [limit, setLimit] = useState("");
  const save = useCellMutation(
    (api, v: { meter: string | null; limit: string }) =>
      api.billing.setBudget({
        meter: v.meter,
        monthly_limit: v.limit,
        currency: v.meter ? null : currency,
        thresholds: [50, 80, 100],
      }),
    { invalidate: [BILLING_KEY], success: "Budget saved. You will be notified at 50%, 80% and 100%.", onSuccess: () => setLimit("") },
  );
  const evaluate = useCellMutation((api) => api.billing.evaluateBudgets(), {
    invalidate: [["alerts"]],
    success: (r) => (r.raised.length ? `${r.raised.length} new notification(s) raised` : "Nothing new to report"),
  });

  const valid = /^\d+(\.\d{1,6})?$/.test(limit.trim()) && Number(limit) > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) save.mutate({ meter: target || null, limit: limit.trim() });
  };

  const columns: Column<UsageBudget>[] = [
    { key: "target", header: "Applies to", pinned: true, render: (b) => (b.meter ? meterLabel(b.meter) : "The whole monthly bill"), sortValue: (b) => b.meter ?? "" },
    { key: "limit", header: "Monthly limit", render: (b) => (b.currency ? money(b.monthly_limit, b.currency) : count(b.monthly_limit)), sortValue: (b) => Number(b.monthly_limit) },
    { key: "thresholds", header: "Notifies at", render: (b) => b.thresholds.map((t) => `${t}%`).join(", ") },
  ];

  return (
    <div className="space-y-4">
      <Banner tone="info" title="A budget only notifies">
        Budgets raise an alert at each threshold, once a month. They never slow down, block or switch off sign-in, token issuing or revocation.
      </Banner>
      <Card title="Set a budget" description="Setting a budget for something that already has one replaces it.">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <Select label="Budget for" options={TARGETS} value={target} onChange={setTarget} className="min-w-64" />
          <TextField
            label={target ? "Monthly limit (count)" : `Monthly limit (${currency})`}
            value={limit}
            onChange={setLimit}
            inputMode="decimal"
            errorMessage={limit && !valid ? "Enter a positive number." : undefined}
            className="w-48"
          />
          <Button type="submit" isDisabled={!valid || save.isPending}>
            Save budget
          </Button>
        </form>
        {save.isError ? <ApiErrorView error={save.error} title="The budget was not saved" onRetry={() => save.reset()} /> : null}
      </Card>
      <QueryView query={budgets}>
        {(data) => (
          <Card
            title="Active budgets"
            padded={false}
            actions={
              <Button variant="secondary" onPress={() => evaluate.mutate(undefined)} isDisabled={evaluate.isPending}>
                Check now
              </Button>
            }
          >
            <DataTable<UsageBudget>
              caption="Active budgets"
              columns={columns}
              rows={data.items}
              rowKey={(b) => b.budget_id}
              empty={<EmptyState title="No budgets" icon={PiggyBank}>Set one above to hear about spending before the month ends.</EmptyState>}
            />
          </Card>
        )}
      </QueryView>
    </div>
  );
}
