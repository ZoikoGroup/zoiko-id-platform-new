"use client";

import { ApiError } from "@zoiko/api-client";
import { AccessUnavailable, Banner, Button, Card, DataTable, EmptyState, LoadingRows, PageHeader } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { outcomeText, parseBudget, problemText, SPEND_KEY, standing } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
    return <Banner tone="danger" title="This could not be done">{problemText(String(error.code ?? ""))}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

function BudgetCard() {
  const budget = useCellQuery([...SPEND_KEY, "budget"], (api) => api.verification.budget());
  const [limit, setLimit] = useState("");
  const [currency, setCurrency] = useState("");
  const set = useCellMutation((api, body: { monthly_limit: string; currency: string }) => api.verification.setBudget(body), {
    invalidate: [SPEND_KEY],
    success: "Monthly limit saved",
  });
  const clear = useCellMutation<void, unknown>((api) => api.verification.clearBudget(), { invalidate: [SPEND_KEY], success: "Monthly limit removed" });
  const parsed = parseBudget(limit, currency);
  const s = standing(budget.data);
  const current = budget.data?.budget;
  return (
    <Card title="Monthly supplier spend limit" description="What the verification suppliers may cost this company each calendar month. At the limit no new verification starts; one already started always finishes.">
      <div className="space-y-3 text-sm">
        {budget.isPending && budget.fetchStatus !== "idle" ? <LoadingRows rows={2} /> : null}
        {budget.isError ? <Failed error={budget.error} /> : null}
        {budget.data ? (
          <Banner tone={s.tone === "neutral" ? "info" : s.tone} title={s.title}>
            {s.text}
            {budget.data.other_currencies?.length ? ` Spend in ${budget.data.other_currencies.join(", ")} is not converted and not counted here.` : ""}
          </Banner>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block font-medium text-ink" htmlFor="vs-limit">Limit per month</label>
            <input id="vs-limit" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder={current?.monthly_limit ?? "2500"} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
          </div>
          <div>
            <label className="block font-medium text-ink" htmlFor="vs-currency">Currency (three letters)</label>
            <input id="vs-currency" value={currency} onChange={(e) => setCurrency(e.target.value)} placeholder={current?.currency ?? "GBP"} maxLength={3} className="w-full rounded-control border border-border bg-surface p-2 text-sm uppercase text-ink" />
          </div>
        </div>
        {limit || currency ? !parsed.ok ? <p className="text-danger">{parsed.reason}</p> : null : null}
        <p className="text-ink-muted">There is no default amount. The number is yours to set; zero means none this month.</p>
        <div className="flex flex-wrap gap-2">
          <Button pending={set.isPending} isDisabled={!parsed.ok} onPress={() => parsed.ok && set.mutate({ monthly_limit: parsed.limit, currency: parsed.currency })}>
            Save limit
          </Button>
          {current ? (
            <Button variant="secondary" pending={clear.isPending} onPress={() => clear.mutate()}>
              Remove the limit
            </Button>
          ) : null}
        </div>
        {set.error ? <Failed error={set.error} /> : null}
        {clear.error ? <Failed error={clear.error} /> : null}
      </div>
    </Card>
  );
}

type Row = { adapter_id: string; status: string; outcome: string | null; sessions: number };

function UsageCard() {
  const usage = useCellQuery([...SPEND_KEY, "usage"], (api) => api.verification.usage());
  const u = usage.data;
  return (
    <Card title="Verification activity, last 30 days" description="Counts only, for Finance and operations: no person, no claim, no evidence. What each attempt cost is in the vendor cost report.">
      <div className="space-y-3 text-sm">
        {usage.isPending && usage.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
        {usage.isError ? <Failed error={usage.error} /> : null}
        {u ? (
          <>
            <p>
              <strong>{u.total_sessions}</strong> verifications, <strong>{u.repeat_attempts}</strong> repeat attempts by the same person, <strong>{u.failover_sessions}</strong> moved to another supplier ({u.failover_verified} of them verified).
            </p>
            <p className="text-ink-muted">
              Review cases: {Object.keys(u.review_cases).length ? Object.entries(u.review_cases).map(([k, n]) => `${n} ${k.toLowerCase().replaceAll("_", " ")}`).join(", ") : "none"}.
            </p>
            <DataTable<Row>
              caption="By supplier and outcome"
              rows={u.sessions}
              rowKey={(r) => `${r.adapter_id}|${r.status}|${r.outcome ?? ""}`}
              empty="No verifications in this period."
              columns={[
                { key: "adapter", header: "Supplier", pinned: true, render: (r) => r.adapter_id },
                { key: "outcome", header: "Outcome", render: (r) => outcomeText(r.status, r.outcome) },
                { key: "n", header: "Verifications", render: (r) => r.sessions },
              ]}
            />
          </>
        ) : null}
      </div>
    </Card>
  );
}

export function VerificationSpendPage() {
  return (
    <>
      <PageHeader
        title="Verification spend"
        description="Set how much identity verification may cost each month and see how much has been used. The platform enforces the limit; it never picks the number for you."
        breadcrumbs={[{ label: "Verification spend" }]}
      />
      <div className="space-y-4">
        <BudgetCard />
        <UsageCard />
      </div>
    </>
  );
}
