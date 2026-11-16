"use client";

import type { GovBudgetRow } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, Meter, Select, Tabs, TextField, type Column } from "@zoiko/ui";
import { Wallet } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AgentGovernanceHeader } from "./shell";
import { DEFAULT_QUOTAS, GOV_KEY, LEVEL, govKey, money, usagePercent, usedAmount, validAmount } from "./logic";

const CURRENCIES = ["USD", "EUR", "GBP", "JPY"].map((c) => ({ id: c, label: c }));

function CapForm({ agentId }: { agentId: string }) {
  const [scope, setScope] = useState<"COMPANY" | "AGENT" | "CALL">("COMPANY");
  const [period, setPeriod] = useState<"DAY" | "MONTH">("DAY");
  const [currency, setCurrency] = useState("USD");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("PILOT");
  const [error, setError] = useState<string | null>(null);

  const set = useCellMutation<void, { state: "ACTIVE" | "PENDING"; hard_cap: string; required_slots?: string[] }>(
    (api) =>
      api.agentGovernance.setBudget({
        scope,
        agent_id: scope === "AGENT" ? agentId : null,
        currency,
        period: scope === "CALL" ? "CALL" : period,
        hard_cap: amount.trim(),
        reason_code: reason.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_") || "PILOT",
      }),
    { invalidate: [GOV_KEY] },
  );

  function submit() {
    const problem = validAmount(amount, currency);
    if (problem) return setError(problem);
    setError(null);
    set.mutate();
  }

  return (
    <Card title="Set a cap" description="A cap that is not higher than the one in force applies now. A higher cap, or the first one, is pending until a finance delegate and Security sign. Never live early.">
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Applies to" options={[{ id: "COMPANY", label: "The whole company" }, { id: "AGENT", label: "This agent" }, { id: "CALL", label: "Each single call" }]} value={scope} onChange={(v) => setScope(v as typeof scope)} />
          <Select label="Window (UTC)" isDisabled={scope === "CALL"} options={[{ id: "DAY", label: "Per UTC day" }, { id: "MONTH", label: "Per UTC month" }]} value={scope === "CALL" ? "DAY" : period} onChange={(v) => setPeriod(v as typeof period)} />
          <Select label="Currency" options={CURRENCIES} value={currency} onChange={setCurrency} description="Approved currencies only." />
          <TextField label="Hard cap" isRequired value={amount} onChange={setAmount} inputMode="decimal" errorMessage={error ?? undefined} description="Exact decimal, for example 5 or 0.10. Hard stop, not a warning." />
          <TextField label="Reason code" value={reason} onChange={setReason} description="Capital letters, digits, underscore." />
        </div>
        <Button pending={set.isPending} onPress={submit}>Save cap</Button>
        <div aria-live="polite">
          {set.data ? (
            set.data.state === "PENDING" ? (
              <Banner tone="info" title="Saved as pending: not live">Waiting for {(set.data.required_slots ?? []).join(" and ").toLowerCase()} to sign under Approvals. Until then the old cap, or no budget, applies.</Banner>
            ) : (
              <Banner tone="ok" title="Cap in force">{money(set.data.hard_cap, currency)} applies from now. Reservations already taken keep what they were given.</Banner>
            )
          ) : null}
        </div>
        {set.error ? <ApiErrorView error={set.error} /> : null}
      </div>
    </Card>
  );
}

export function LimitsEditor({ agentId }: { agentId: string }) {
  const budget = useCellQuery(govKey("budget", agentId), (a) => a.agentGovernance.budget(agentId), { refetchInterval: 30000 });
  const access = useCellQuery(govKey("access", agentId), (a) => a.agentGovernance.effectiveAccess(agentId));
  const [tab, setTab] = useState("spending");

  const columns: Column<GovBudgetRow>[] = [
    { key: "name", header: "Ceiling", pinned: true, render: (b) => <span className="font-medium">{b.scope} · {b.window === "CALL" ? "per call" : b.window === "DAY" ? "per UTC day" : "per UTC month"}</span> },
    { key: "cap", header: "Hard cap", render: (b) => <span className="tabular">{money(b.hard_cap, b.currency)}</span> },
    { key: "committed", header: "Committed", render: (b) => <span className="tabular">{money(b.committed, b.currency)}</span> },
    { key: "reserved", header: "Reserved (in flight)", render: (b) => <span className="tabular">{money(b.reserved, b.currency)}</span> },
    { key: "available", header: "Available", render: (b) => <span className="tabular font-medium">{money(b.available, b.currency)}</span> },
    {
      key: "level",
      header: "Level",
      render: (b) => {
        const l = LEVEL[b.level];
        return (
          <span className="flex min-w-40 flex-col gap-1">
            <Badge size="xs" tone={l.tone}>{l.label}</Badge>
            <Meter label={`${b.name} used`} tone={l.tone === "neutral" ? "brand" : l.tone === "info" ? "brand" : l.tone} value={usagePercent(usedAmount(b), b.hard_cap)} valueText={`${usagePercent(usedAmount(b), b.hard_cap)}%`} />
          </span>
        );
      },
    },
    { key: "reset", header: "Resets (UTC)", render: (b) => (b.resets_at ? <span className="tabular text-xs">{b.resets_at.replace("T", " ").slice(0, 16)}</span> : "Each call") },
  ];

  return (
    <>
      <AgentGovernanceHeader
        agentId={agentId}
        view="limits"
        title="Usage and spending"
        description="Caps are hard runtime limits, not alerts. Committed, reserved and available are shown apart, and a pending change is never shown as live."
      />
      <div className="space-y-4">
        <Banner tone="info" title="What this agent can spend today">
          AI cost: $0 until a model is approved and finance sets a cap. External payments and paid vendor checks: never. Alerts never lift a hard cap.
        </Banner>
        <Tabs
          label="Usage and spending"
          selectedTab={tab}
          onSelectionChange={setTab}
          tabs={[
            {
              id: "spending",
              label: "Spending",
              content: (
                <div className="space-y-4">
                  <QueryView query={budget}>
                    {(b) => (
                      <>
                        <Card padded={false} title="Ceilings above this agent" description={`As of ${b.as_of.replace("T", " ").slice(0, 19)} ${b.timezone}. The effective limit is the lowest applicable ceiling.`}>
                          <DataTable caption="Spending ceilings" columns={columns} rows={b.ceilings} rowKey={(r) => r.budget_id} empty={<EmptyState icon={Wallet} title="No cap is in force">With no cap, no paid step can be reserved. That is the safe default.</EmptyState>} />
                        </Card>
                        {b.pending.length ? (
                          <Banner tone="warn" title={`${b.pending.length} cap change(s) waiting for signatures`}>
                            Pending, never live: {b.pending.map((p) => `${p.scope} ${p.period.toLowerCase()} ${money(p.hard_cap, p.currency)}`).join("; ")}. Decide them under Approvals.
                          </Banner>
                        ) : null}
                      </>
                    )}
                  </QueryView>
                  <CapForm agentId={agentId} />
                </div>
              ),
            },
            {
              id: "usage",
              label: "Usage",
              content: (
                <QueryView query={access}>
                  {(a) => (
                    <Card padded={false} title="Run limits" description="The most restrictive of the agent's policy and the company baseline applies. Changing them is a policy change: request it under Permissions.">
                      <DataTable
                        caption="Run limits"
                        rows={Object.entries(DEFAULT_QUOTAS).map(([key, label]) => ({
                          key,
                          label,
                          policy: a.policy?.quotas[key],
                          company: a.company_baseline?.quotas[key],
                        }))}
                        rowKey={(r) => r.key}
                        columns={[
                          { key: "l", header: "Limit", pinned: true, render: (r) => r.label },
                          { key: "p", header: "This agent's policy", render: (r) => String(r.policy ?? "not set") },
                          { key: "c", header: "Company baseline", render: (r) => String(r.company ?? "not set") },
                        ]}
                      />
                    </Card>
                  )}
                </QueryView>
              ),
            },
          ]}
        />
      </div>
    </>
  );
}
