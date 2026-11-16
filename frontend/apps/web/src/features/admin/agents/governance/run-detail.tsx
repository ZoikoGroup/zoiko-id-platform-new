"use client";

import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, Stat, Tabs } from "@zoiko/ui";
import { ClipboardList } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { PROPOSAL_STATUS, SEVERITY, daaraKey, runHeadline, runLabel, timelineStatus } from "./daara-logic";
import { codeText } from "./logic";
import { PauseDialog } from "./pause-dialog";
import { AgentGovernanceHeader } from "./shell";

/** One run (WF-AG-03): what it read, what it decided, what it proposed, and whether its evidence is whole. */
export function RunDetailPage({ agentId, runId }: { agentId: string; runId: string }) {
  const run = useCellQuery(daaraKey("run", agentId, runId), (a) => a.daara.run(agentId, runId));
  const [pausing, setPausing] = useState(false);
  return (
    <>
      <AgentGovernanceHeader
        agentId={agentId}
        view="daara"
        title="Access readiness run"
        description="Read-only for most people. Pausing future runs is checked separately."
        actions={<Button variant="danger" onPress={() => setPausing(true)}>Pause future runs</Button>}
      />
      <QueryView query={run}>
        {(r) => {
          const label = runLabel(r);
          const steps = timelineStatus(r.timeline.map((t) => t.step));
          const first = r.proposals_for_run[0];
          return (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={label.tone}>{label.label}</Badge>
                <span className="font-mono text-xs text-ink-subtle">Run {r.run_id}</span>
                {r.reason_code ? <Badge tone="danger" size="xs">{r.reason_code}</Badge> : null}
              </div>
              {r.redacted ? (
                <Banner tone="info" title="Redacted for your role">
                  You can see the result, not the individual policy decisions. This is not an empty success.
                </Banner>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Run summary">
                <Stat label="Records checked" value={r.checked} />
                <Stat label="Issues flagged" value={r.issues} />
                <Stat label="Review proposals" value={r.proposals} />
                <Stat label="Privileges changed" value={r.privileges_changed} hint="Always zero: this agent cannot change access" />
              </div>
              <p className="text-sm text-ink-muted">{runHeadline(r)}</p>
              <Tabs
                label="Run details"
                tabs={[
                  {
                    id: "overview",
                    label: "Overview",
                    content: (
                      <div className="space-y-4">
                        <Card title="What happened" description={r.model_used ? "Written by an approved model, then checked against the findings." : "Fixed text built from the counts. No model was used."}>
                          <p className="text-sm">{r.explanation}</p>
                        </Card>
                        <Card padded={false} title="Timeline">
                          <ol className="space-y-2 p-4">
                            {steps.map((s) => (
                              <li key={s.step} className="flex items-center gap-2 text-sm">
                                <Badge size="xs" tone={s.done ? "ok" : "neutral"}>{s.done ? "Done" : "Not reached"}</Badge>
                                {s.label}
                              </li>
                            ))}
                          </ol>
                        </Card>
                        <Card padded={false} title="Proposals from this run">
                          <DataTable
                            caption="Review proposals from this run"
                            rows={r.proposals_for_run}
                            rowKey={(p) => p.proposal_id}
                            empty={<EmptyState icon={ClipboardList} title="No proposal">Nothing needed a person's review in this run.</EmptyState>}
                            columns={[
                              { key: "ref", header: "Proposal", render: (p) => <Link className="font-medium text-brand-700 underline" href={`/admin/agents/review/${p.proposal_id}`}>{p.ref}</Link> },
                              { key: "t", header: "Target", render: (p) => <span className="font-mono text-xs">{p.target}</span> },
                              { key: "f", header: "Finding", render: (p) => p.finding_code.replaceAll("_", " ").toLowerCase() },
                              { key: "s", header: "Severity", render: (p) => <Badge size="xs" tone={SEVERITY[p.severity].tone}>{SEVERITY[p.severity].label}</Badge> },
                              { key: "st", header: "Status", render: (p) => PROPOSAL_STATUS[p.status].label },
                            ]}
                          />
                        </Card>
                      </div>
                    ),
                  },
                  {
                    id: "decisions",
                    label: "Policy decisions",
                    content: r.redacted ? (
                      <p className="text-sm text-ink-muted">Not shown for your role.</p>
                    ) : (
                      <DataTable
                        caption="Policy decisions"
                        rows={r.policy_decisions}
                        rowKey={(x) => String(x.seq)}
                        empty={<EmptyState icon={ClipboardList} title="No decision recorded">A run records one for every governed step.</EmptyState>}
                        columns={[
                          { key: "n", header: "#", render: (x) => x.seq },
                          { key: "a", header: "Decision", render: (x) => x.action },
                          { key: "o", header: "Outcome", render: (x) => (x.outcome === "SHADOW_DENY" ? "Would be refused (shadow mode)" : x.outcome.toLowerCase()) },
                          { key: "c", header: "Reason", render: (x) => (x.code ? `${x.code}: ${codeText(x.code)}` : "—") },
                          { key: "h", header: "Policy", render: (x) => <span className="font-mono text-2xs">{x.policy_hash?.slice(0, 19) ?? "—"}…</span> },
                        ]}
                      />
                    ),
                  },
                  {
                    id: "evidence",
                    label: "Evidence",
                    content: (
                      <div className="space-y-3">
                        {r.evidence.complete ? (
                          <Banner tone="ok" title="Evidence complete">{r.evidence.events} record(s), every expected step present.</Banner>
                        ) : (
                          <Banner tone="danger" title="Integrity exception" live="assertive">
                            Missing: {r.evidence.missing.join(", ")}. A gap is shown as a gap, never as a green indicator.
                          </Banner>
                        )}
                        <KeyValues columns={2} items={[["Rule set", `v${r.rule_set_version}`], ["Policy hash", <span key="p" className="break-all font-mono text-2xs">{r.policy_hash ?? "none"}</span>]]} />
                        <Link className="text-sm font-medium text-brand-700 underline" href={`/admin/agents/governance/${agentId}/monitoring`}>Verify the whole evidence chain</Link>
                      </div>
                    ),
                  },
                  {
                    id: "technical",
                    label: "Technical events",
                    content: (
                      <DataTable
                        caption="Technical events"
                        rows={r.timeline}
                        rowKey={(t) => String(t.seq)}
                        columns={[
                          { key: "n", header: "#", render: (t) => t.seq },
                          { key: "at", header: "When (UTC)", render: (t) => <span className="tabular text-xs">{(t.at ?? "").replace("T", " ").slice(0, 19)}</span> },
                          { key: "s", header: "Step", render: (t) => t.step },
                          { key: "o", header: "Outcome", render: (t) => t.outcome.toLowerCase() },
                        ]}
                      />
                    ),
                  },
                ]}
              />
              {first ? (
                <Link className="inline-block text-sm font-medium text-brand-700 underline" href={`/admin/agents/review/${first.proposal_id}`}>
                  Open review proposal {first.ref}
                </Link>
              ) : null}
            </div>
          );
        }}
      </QueryView>
      <PauseDialog isOpen={pausing} onClose={() => setPausing(false)} agentId={agentId} />
    </>
  );
}
