"use client";

import type { GovAgentRow, GovAttentionItem } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, PageHeader, Select, StatusBadge, Stat, buttonClass, type Column } from "@zoiko/ui";
import { Bot, OctagonAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { GovernanceNav } from "./shell";
import { GOV_KEY, RULE_TEXT, SEVERITY_TONE, TELEMETRY, govKey, money } from "./logic";
import { PauseDialog } from "./pause-dialog";

const ROUTES = [
  { id: "SECURITY", label: "Security" },
  { id: "AUTHORITY", label: "Authority" },
  { id: "PRIVACY", label: "Privacy" },
  { id: "FINANCE", label: "Finance" },
  { id: "SRE", label: "SRE" },
];

/** Acknowledge says "a person has seen this"; it clears nothing and lifts no limit. Route gives it a team. */
function AlertActions({ item }: { item: GovAttentionItem }) {
  const [route, setRoute] = useState("");
  const key = item.alert_key;
  const ack = useCellMutation<void, { state: string }>((api) => api.daara.acknowledgeAlert(key ?? ""), { invalidate: [GOV_KEY], success: "Acknowledged" });
  const send = useCellMutation<void, { state: string }>(
    (api) => api.daara.routeAlert(key ?? "", { route: route as "SECURITY" | "AUTHORITY" | "PRIVACY" | "FINANCE" | "SRE" }),
    { invalidate: [GOV_KEY], success: "Routed" },
  );
  if (!key || !item.tracked || item.state === "RESOLVED") return null;
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <Button size="xs" variant="secondary" isDisabled={item.state !== "OPEN"} pending={ack.isPending} onPress={() => ack.mutate()}>
        Acknowledge
      </Button>
      <Select label="Route to" options={ROUTES} value={route || item.route || null} onChange={setRoute} placeholder="Team…" />
      <Button size="xs" variant="secondary" isDisabled={!route} pending={send.isPending} onPress={() => send.mutate()}>
        Route
      </Button>
      {ack.error ? <ApiErrorView error={ack.error} /> : null}
      {send.error ? <ApiErrorView error={send.error} /> : null}
    </div>
  );
}

function Attention({ items }: { items: GovAttentionItem[] }) {
  return (
    <Card title="Requires attention" description="Computed now from live state. Each alert has an owner and a response time.">
      {items.length ? (
        <ul className="space-y-4">
          {items.map((i, n) => (
            <li key={`${i.alert_key ?? i.rule}-${n}`} className="flex items-start gap-3">
              <Badge tone={SEVERITY_TONE[i.severity]} size="xs">{i.severity}</Badge>
              <div className="min-w-0 text-sm">
                <p className="font-medium text-ink">{RULE_TEXT[i.rule] ?? i.rule}</p>
                <p className="text-xs text-ink-muted">{i.detail}</p>
                <p className="mt-1 flex flex-wrap items-center gap-1 text-xs">
                  <Badge size="xs" tone={i.state === "OPEN" ? "warn" : "info"}>
                    {i.state === "ACKNOWLEDGED" ? "Acknowledged" : i.state === "ROUTED" ? `Routed${i.route ? ` to ${i.route.toLowerCase()}` : ""}` : "Open"}
                  </Badge>
                  {i.sla_breached ? <Badge size="xs" tone="danger">Past its response time</Badge> : i.sla_due ? <span className="text-ink-muted">Respond by {i.sla_due.replace("T", " ").slice(0, 16)} UTC</span> : null}
                  {i.occurrences && i.occurrences > 1 ? <span className="text-ink-muted">Seen {i.occurrences} times</span> : null}
                </p>
                {i.agent_id ? (
                  <Link className="text-xs font-medium text-brand-700 underline" href={`/admin/agents/governance/${i.agent_id}/monitoring`}>
                    Open agent
                  </Link>
                ) : null}
                <AlertActions item={i} />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">Nothing needs attention right now.</p>
      )}
    </Card>
  );
}

function RecentDecisions() {
  const events = useCellQuery(govKey("events", "fleet"), (a) => a.agentGovernance.events(null, { limit: 10 }));
  return (
    <Card padded={false} title="Recent policy decisions" description="The newest evidence records, in the order they were written.">
      <QueryView query={events}>
        {(data) => (
          <DataTable
            caption="Recent policy decisions"
            rows={data.items}
            rowKey={(e) => e.event_id}
            dense
            empty={<EmptyState icon={OctagonAlert} title="No evidence yet">Evidence appears here after the first governed call or approval.</EmptyState>}
            columns={[
              { key: "at", header: "When (UTC)", render: (e) => <span className="tabular text-xs">{e.occurred_at.replace("T", " ").slice(0, 19)}</span> },
              { key: "action", header: "Event", render: (e) => e.action },
              { key: "outcome", header: "Outcome", render: (e) => <Badge size="xs" tone={e.outcome === "DENY" ? "danger" : e.outcome === "SHADOW_DENY" ? "warn" : e.outcome === "ALLOW" ? "ok" : "neutral"}>{e.outcome === "SHADOW_DENY" ? "Would be refused" : e.outcome.toLowerCase()}</Badge> },
              { key: "code", header: "Reason", render: (e) => e.code ?? "—" },
            ]}
          />
        )}
      </QueryView>
    </Card>
  );
}

export function GovernanceOverview() {
  const overview = useCellQuery(govKey("overview"), (a) => a.agentGovernance.overview(), { refetchInterval: 30000 });
  const [pausing, setPausing] = useState(false);

  const columns: Column<GovAgentRow>[] = [
    {
      key: "name",
      header: "Agent",
      pinned: true,
      render: (a) => (
        <Link className="font-medium text-brand-700 underline" href={`/admin/agents/governance/${a.agent_id}/permissions`}>
          {a.name ?? a.agent_id.slice(0, 8)}
        </Link>
      ),
      sortValue: (a) => a.name ?? "",
    },
    { key: "sponsor", header: "Sponsor", render: (a) => (a.sponsor ? <span className="font-mono text-xs">{a.sponsor.slice(0, 8)}…</span> : "None") },
    { key: "last", header: "Last evidence (UTC)", render: (a) => (a.last_evidence_at ? <span className="tabular text-xs">{a.last_evidence_at.replace("T", " ").slice(0, 19)}</span> : "None yet") },
    { key: "state", header: "State", render: (a) => <StatusBadge state={a.state} kind="Agent status" />, sortValue: (a) => a.state },
    { key: "risk", header: "Risk", render: (a) => (a.risk ? <Badge size="xs" tone={a.risk === "HIGH" ? "danger" : a.risk === "MEDIUM" ? "warn" : "neutral"}>{a.risk.toLowerCase()}</Badge> : "No policy") },
    { key: "spend", header: "Spend today", render: (a) => <span className="tabular">{money(a.spend_today, "USD")}</span>, sortValue: (a) => Number(a.spend_today) },
    {
      key: "governed",
      header: "Governed",
      render: (a) => (a.governed ? <Badge size="xs" tone="ok">Policy v{a.policy_version}</Badge> : <Badge size="xs" tone="warn">No live policy</Badge>),
    },
  ];

  return (
    <>
      <PageHeader
        icon={Bot}
        title="Agent governance"
        description="What every agent may do, what it may spend, who approved it, and how to stop it. One workspace, five pages."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Governance" }]}
        actions={
          <span className="flex flex-wrap gap-2">
            <a className={buttonClass("secondary", "md")} href="/api/bff/v1/machine/governance/events:export" download>
              Export redacted report (CSV)
            </a>
            <Button variant="danger" onPress={() => setPausing(true)}>
              Emergency pause
            </Button>
          </span>
        }
      />
      <GovernanceNav current="overview" />
      <QueryView query={overview}>
        {(o) => {
          const tel = TELEMETRY[o.telemetry.state];
          return (
            <div className="space-y-4">
              {o.mode !== "enforce" ? (
                <Banner tone="warn" title={o.mode === "shadow" ? "Governance is in shadow mode" : "Governance checks are off"}>
                  {o.mode === "shadow"
                    ? "Refusals are recorded as 'would be refused' and the call still goes through. An emergency stop is enforced in every mode. The owners switch to enforce."
                    : "No policy, quota or budget check runs. An emergency stop is still enforced."}
                </Banner>
              ) : null}
              {o.telemetry.state !== "CURRENT" ? (
                <Banner tone="warn" title={tel.label} live="polite">
                  {o.telemetry.message ?? "Health is shown as unknown, not as healthy."}
                </Banner>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-label="Summary">
                <Stat label="Active agents" value={o.kpis.active_agents} />
                <Stat label="Pending approvals" value={o.kpis.pending_approvals} hint={<Link className="underline" href="/admin/agents/governance/approvals">Open the queue</Link>} />
                <Stat label="Denied tool calls (24 h)" value={o.kpis.denied_calls_24h} hint={o.kpis.shadow_denials_24h ? `${o.kpis.shadow_denials_24h} more would be refused in enforce mode` : undefined} />
                <Stat label="Budget used today" value={money(o.kpis.budget_used_today, "USD")} hint="Committed plus reserved" />
                <Stat label="Risk signals" value={o.kpis.risk_signals} />
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="lg:col-span-2">
                  <Card padded={false} title="Agent registry" description={`As of ${o.as_of.replace("T", " ").slice(0, 19)} ${o.timezone}. Counts are ${o.counts_source}.`}>
                    <DataTable
                      caption="Agent registry"
                      columns={columns}
                      rows={o.agents}
                      rowKey={(a) => a.agent_id}
                      empty={
                        <EmptyState icon={Bot} title="No agents registered" action={<Link className="text-sm font-medium text-brand-700 underline" href="/admin/agents">Register an agent</Link>}>
                          Register an agent first; its governance appears here.
                        </EmptyState>
                      }
                    />
                  </Card>
                </div>
                <Attention items={o.requires_attention} />
              </div>
              <RecentDecisions />
              <p className="text-xs text-ink-subtle">
                Data freshness: {tel.label}
                {o.telemetry.last_ingest ? `, last evidence ${o.telemetry.last_ingest.replace("T", " ").slice(0, 19)} UTC` : ""}.
              </p>
            </div>
          );
        }}
      </QueryView>
      <PauseDialog isOpen={pausing} onClose={() => setPausing(false)} />
    </>
  );
}

