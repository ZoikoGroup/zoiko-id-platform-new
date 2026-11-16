"use client";

import type { ReadinessProposal, ReadinessSetup } from "@zoiko/api-client";
import { Badge, Banner, ButtonLink, Card, DateTime, EmptyState, PageHeader, StatusBadge } from "@zoiko/ui";
import { Bot, Plus } from "lucide-react";
import Link from "next/link";

import { useCellQuery } from "@/lib/cell";

import { READINESS_KEY, RULE_TEXT, RUN_STATE_TEXT, severityTone } from "./logic";
import { NO_CHANGE_NOTICE } from "./proposal-card";
import { ReadinessQuery } from "./shared";

function Runs({ agentId }: { agentId: string }) {
  const runs = useCellQuery([...READINESS_KEY, "runs", agentId], (api) => api.accessReadiness.runs(agentId));
  return (
    <ReadinessQuery query={runs} loadingRows={2}>
      {(data) =>
        data.items.length ? (
          <ul className="divide-y divide-border text-sm">
            {data.items.map((r) => (
              <li key={r.run_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <Link className="inline-flex min-h-11 items-center font-medium text-brand-700 underline" href={`/admin/agents/readiness/${encodeURIComponent(agentId)}/runs/${encodeURIComponent(r.run_id)}`}>
                  Run {r.run_id.slice(0, 8)}
                </Link>
                <Badge tone={r.state === "DENIED" || r.state === "FAILED" ? "danger" : r.state === "COMPLETED" ? "ok" : "neutral"}>{RUN_STATE_TEXT[r.state]}</Badge>
                <DateTime value={r.created_at} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No runs yet.</p>
        )
      }
    </ReadinessQuery>
  );
}

function SetupRow({ s }: { s: ReadinessSetup }) {
  return (
    <Card
      title={s.display_name}
      description={`Agent ${s.agent_id.slice(0, 8)}… · region ${s.scope.home_region}`}
      actions={
        <span className="flex flex-wrap items-center gap-2">
          <StatusBadge state={s.status} kind="Setup status" />
          <ButtonLink size="lg" href={`/admin/agents/readiness/${encodeURIComponent(s.agent_id)}/setup`}>
            Open setup
          </ButtonLink>
        </span>
      }
    >
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-ink-subtle">Runs</h3>
      <Runs agentId={s.agent_id} />
    </Card>
  );
}

function ProposalRow({ p }: { p: ReadinessProposal }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
      <Link className="inline-flex min-h-11 items-center font-medium text-brand-700 underline" href={`/admin/agent-review/${encodeURIComponent(p.proposal_id)}`}>
        {p.rule_id} {RULE_TEXT[p.rule_id] ?? p.finding_code}
      </Link>
      <code className="font-mono text-xs">{p.target}</code>
      <Badge tone={severityTone(p.severity)}>Severity: {p.severity}</Badge>
      <Badge>{p.status.replace("_", " ").toLowerCase()}</Badge>
    </li>
  );
}

/** Where the Access Readiness agent starts: its setups with their runs, and the review proposals waiting for a person. */
export function ReadinessHomePage() {
  const setups = useCellQuery(READINESS_KEY, (api) => api.accessReadiness.setups());
  const proposals = useCellQuery([...READINESS_KEY, "proposals"], (api) => api.accessReadiness.proposals());
  return (
    <>
      <PageHeader
        title="Access readiness agent"
        description="Checks mandate metadata against fixed rules and proposes reviews for a person. It cannot change anyone's access."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Access readiness" }]}
        actions={
          <ButtonLink size="lg" variant="primary" icon={<Plus aria-hidden className="size-4" />} href="/admin/agents/readiness/new">
            Create agent
          </ButtonLink>
        }
      />
      <div className="space-y-5">
        <ReadinessQuery query={setups}>
          {(data) =>
            data.items.length ? (
              <div className="space-y-4">{data.items.map((s) => <SetupRow key={s.setup_id} s={s} />)}</div>
            ) : (
              <EmptyState icon={Bot} title="No access readiness agent set up yet">
                Create one from an agent you already registered. Nothing runs until a second person approves it.
              </EmptyState>
            )
          }
        </ReadinessQuery>
        <Card title="Review proposals" description="Suggestions an agent made for a person to review.">
          <Banner tone="info" title="No access permissions will change">
            {NO_CHANGE_NOTICE}
          </Banner>
          <div className="mt-3">
            <ReadinessQuery query={proposals} loadingRows={2}>
              {(data) =>
                data.items.length ? (
                  <ul className="divide-y divide-border">{data.items.map((p) => <ProposalRow key={p.proposal_id} p={p} />)}</ul>
                ) : (
                  <p className="text-sm text-ink-muted">No proposals yet.</p>
                )
              }
            </ReadinessQuery>
          </div>
        </Card>
      </div>
    </>
  );
}
