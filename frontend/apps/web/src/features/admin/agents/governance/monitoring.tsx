"use client";

import type { GovEvent } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, Stat, buttonClass, type Column } from "@zoiko/ui";
import { Activity } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { AgentGovernanceHeader } from "./shell";
import { GOV_KEY, RULE_TEXT, SEVERITY_TONE, TELEMETRY, codeText, govKey } from "./logic";
import { PauseDialog } from "./pause-dialog";

function EventDetail({ event }: { event: GovEvent }) {
  return (
    <Card title="Event detail" description="From the immutable evidence stream. Fields your role may not see are left out.">
      <KeyValues
        columns={2}
        items={[
          ["Event", <span key="e" className="font-mono text-xs">{event.event_id}</span>],
          ["Sequence", String(event.seq)],
          ["Action", event.action],
          ["Outcome", event.outcome === "SHADOW_DENY" ? "Would be refused (shadow mode)" : event.outcome.toLowerCase()],
          ["Reason", event.code ? `${event.code}: ${codeText(event.code)}` : null],
          ["Tool", event.tool_id ?? null],
          ["Resource", event.resource ?? null],
          ["Region", event.region ?? null],
          ["Policy hash", event.policy_hash ? <span key="p" className="break-all font-mono text-2xs">{event.policy_hash}</span> : null],
          ["Trace", event.correlation_id ?? null],
          ["Digest", <span key="d" className="break-all font-mono text-2xs">{event.digest}</span>],
          ["Cost receipt", event.cost_receipt ? <span key="c" className="font-mono text-2xs">{JSON.stringify(event.cost_receipt)}</span> : null],
        ]}
      />
    </Card>
  );
}

export function MonitoringView({ agentId }: { agentId: string }) {
  const monitor = useCellQuery(govKey("monitor", agentId), (a) => a.agentGovernance.monitoring(agentId), { refetchInterval: 30000 });
  const events = useCellQuery(govKey("events", agentId), (a) => a.agentGovernance.events(agentId, { limit: 25 }));
  const [selected, setSelected] = useState<GovEvent | null>(null);
  const [pausing, setPausing] = useState(false);
  const verify = useCellMutation<void, { intact: boolean; broken_at: number | null; checked: number }>((api) => api.agentGovernance.verifyEvidence());
  const restore = useCellMutation<string, { ref?: string }>((api, caseId) => api.agentGovernance.requestRestore(caseId), {
    invalidate: [GOV_KEY],
    success: "Restore requested. It needs two independent people.",
  });

  const columns: Column<GovEvent>[] = [
    { key: "at", header: "When (UTC)", render: (e) => <span className="tabular text-xs">{e.occurred_at.replace("T", " ").slice(0, 19)}</span> },
    { key: "action", header: "Event", render: (e) => e.action },
    {
      key: "outcome",
      header: "Outcome",
      render: (e) => <Badge size="xs" tone={e.outcome === "DENY" ? "danger" : e.outcome === "SHADOW_DENY" ? "warn" : e.outcome === "ALLOW" ? "ok" : "neutral"}>{e.outcome === "SHADOW_DENY" ? "Would be refused" : e.outcome.toLowerCase()}</Badge>,
    },
    { key: "code", header: "Reason", render: (e) => e.code ?? "—" },
    { key: "seq", header: "#", render: (e) => <span className="tabular text-xs">{e.seq}</span> },
  ];

  return (
    <>
      <AgentGovernanceHeader
        agentId={agentId}
        view="monitoring"
        title="Monitoring and incident controls"
        description="What the agent did, what was refused, and the controls to stop it. A quiet or missing feed is shown as unknown, never as healthy."
        actions={
          <span className="flex flex-wrap gap-2">
            <a className={buttonClass("secondary", "md")} href={`/api/bff/v1/machine/agents/${agentId}/events:export`} download>
              Export redacted evidence (CSV)
            </a>
            <Button variant="danger" onPress={() => setPausing(true)}>Pause agent</Button>
          </span>
        }
      />
      <QueryView query={monitor}>
        {(m) => {
          const tel = TELEMETRY[m.telemetry.state];
          return (
            <div className="space-y-4">
              {m.incident.length ? (
                <Banner tone="danger" title="Emergency stop in force" live="assertive">
                  {m.incident.map((i) => `${i.scope} stop, generation ${i.generation}: ${i.reason}`).join("; ")}
                  <div className="mt-2 flex gap-2">
                    {m.incident.map((i) => (
                      <Button key={i.case_id} size="sm" pending={restore.isPending} onPress={() => restore.mutate(i.case_id)}>Request restore</Button>
                    ))}
                  </div>
                </Banner>
              ) : null}
              {m.telemetry.state !== "CURRENT" ? <Banner tone="warn" title={tel.label} live="polite">No recent evidence for this agent. Do not read this as healthy.</Banner> : null}
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Allowed (24 h)" value={m.summary.allowed_24h} />
                <Stat label="Denied (24 h)" value={m.summary.denied_24h} />
                <Stat label="Would be refused (24 h)" value={m.summary.shadow_denied_24h} hint="Shadow mode only" />
                <Stat label="Running now" value={m.active_runs.length} />
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="space-y-4 lg:col-span-2">
                  <Card padded={false} title="Evidence timeline" description="Newest first. Select a row for its detail.">
                    <QueryView query={events}>
                      {(ev) => (
                        <>
                          {ev.redacted ? <p className="px-4 pt-3 text-xs text-ink-muted">Your role sees a redacted stream: resource, actor, tool and cost are left out.</p> : null}
                          <DataTable caption="Agent evidence events" columns={columns} rows={ev.items} rowKey={(e) => e.event_id} onRowAction={setSelected} selectedKey={selected?.event_id ?? null} empty={<EmptyState icon={Activity} title="No evidence yet">Evidence appears after the first governed call.</EmptyState>} />
                        </>
                      )}
                    </QueryView>
                  </Card>
                  {selected ? <EventDetail event={selected} /> : null}
                </div>
                <div className="space-y-4">
                  <Card title="Alerts and incident state">
                    {m.alerts.length ? (
                      <ul className="space-y-3">
                        {m.alerts.map((a, i) => (
                          <li key={i} className="flex items-start gap-3">
                            <Badge size="xs" tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge>
                            <div className="text-sm"><p className="font-medium">{RULE_TEXT[a.rule] ?? a.rule}</p><p className="text-xs text-ink-muted">{a.detail}</p></div>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-ink-muted">No alert right now.</p>
                    )}
                  </Card>
                  <Card title="Sponsor and expiry">
                    <KeyValues columns={1} items={[["Sponsor", m.sponsor.principal_id ? <span key="s" className="font-mono text-xs">{m.sponsor.principal_id}</span> : "None"], ["Sponsor status", m.sponsor.active ? "Active" : "Not active: the agent cannot run"], ["Agent expires", m.expires_at ? m.expires_at.slice(0, 10) : "Not set"], ["Policy", m.policy ? `Version ${m.policy.version}` : "None live"]]} />
                  </Card>
                  <Card title="Evidence integrity" description="Recomputes every digest in the chain.">
                    <Button variant="secondary" pending={verify.isPending} onPress={() => verify.mutate()}>Verify the chain</Button>
                    <div aria-live="polite" className="mt-3">
                      {verify.data ? (
                        verify.data.intact ? (
                          <Banner tone="ok" title="Chain intact">{verify.data.checked} record(s) checked.</Banner>
                        ) : (
                          <Banner tone="danger" title="Integrity exception">The chain breaks at record {verify.data.broken_at}. This is an incident, not a warning.</Banner>
                        )
                      ) : null}
                    </div>
                  </Card>
                </div>
              </div>
              <p className="text-xs text-ink-subtle">As of {m.as_of.replace("T", " ").slice(0, 19)} UTC. {tel.label}.</p>
            </div>
          );
        }}
      </QueryView>
      {restore.error ? <ApiErrorView error={restore.error} /> : null}
      <PauseDialog isOpen={pausing} onClose={() => setPausing(false)} agentId={agentId} />
    </>
  );
}
