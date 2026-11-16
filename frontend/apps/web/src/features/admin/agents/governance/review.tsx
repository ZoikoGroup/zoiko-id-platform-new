"use client";

import { ApiError, type ReviewProposal } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, KeyValues, PageHeader, Select } from "@zoiko/ui";
import { ClipboardList } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PersonSelect } from "../pickers";
import { DAARA_KEY, DISMISS_REASONS, FINDING_TEXT, PROPOSAL_STATUS, SEVERITY, daaraKey } from "./daara-logic";
import { GOV_KEY } from "./logic";
import { GovernanceNav } from "./shell";

type Filter = "OPEN" | "ALL";

/** The queue of suggestions the agent made. Reviewing here is not an identity-administration shortcut. */
export function ReviewQueuePage() {
  const [filter, setFilter] = useState<Filter>("OPEN");
  const queue = useCellQuery(daaraKey("queue", filter), (a) => a.daara.queue(filter === "OPEN" ? "OPEN" : undefined), { refetchInterval: 30000 });
  return (
    <>
      <PageHeader
        icon={ClipboardList}
        title="Agent review queue"
        description="Suggestions an agent made for a person to decide. Nothing here changes anyone's access."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Governance", href: "/admin/agents/governance" }, { label: "Review queue" }]}
      />
      <GovernanceNav current="review" />
      {queue.isError && queue.error instanceof ApiError && queue.error.status === 404 ? (
        <EmptyState icon={ClipboardList} title="Replaced by the Access readiness agent">
          This reference agent was retired: the Access readiness agent is now the one agent for this review, and its proposals are listed there. Nothing
          was deleted. Open the{" "}
          <Link className="font-medium text-brand-700 underline" href="/admin/agents/readiness">
            Access readiness agent
          </Link>
          .
        </EmptyState>
      ) : (
      <QueryView query={queue}>
        {(q) => (
          <div className="space-y-4">
            <Banner tone="info" title="No access permissions change from a proposal">{q.notice}</Banner>
            <Card
              padded={false}
              title={`${q.open} open`}
              actions={<Select label="Show" options={[{ id: "OPEN", label: "Open" }, { id: "ALL", label: "All" }]} value={filter} onChange={(v) => setFilter(v as Filter)} />}
            >
              <DataTable
                caption="Review proposals"
                rows={q.items}
                rowKey={(p) => p.proposal_id}
                empty={<EmptyState icon={ClipboardList} title="Nothing to review">Proposals appear here after an access readiness run finds something.</EmptyState>}
                columns={[
                  { key: "ref", header: "Suggestion", pinned: true, render: (p) => <Link className="font-medium text-brand-700 underline" href={`/admin/agents/review/${p.proposal_id}`}>{p.ref}</Link> },
                  { key: "by", header: "Submitted by", render: () => "Agent (not a person)" },
                  { key: "t", header: "Target", render: (p) => <span className="font-mono text-xs">{p.target}</span> },
                  { key: "f", header: "Finding", render: (p) => FINDING_TEXT[p.finding_code] ?? p.finding_code },
                  { key: "s", header: "Severity", render: (p) => <Badge size="xs" tone={SEVERITY[p.severity].tone}>{SEVERITY[p.severity].label}</Badge> },
                  { key: "n", header: "Seen", render: (p) => `${p.seen_count}×` },
                  { key: "st", header: "Status", render: (p) => <Badge size="xs" tone={PROPOSAL_STATUS[p.status].tone}>{PROPOSAL_STATUS[p.status].label}</Badge> },
                ]}
              />
            </Card>
          </div>
        )}
      </QueryView>
      )}
    </>
  );
}

function Decision({ p }: { p: ReviewProposal }) {
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState("");
  const [done, setDone] = useState<{ status: string; notice: string; digest: string | null } | null>(null);
  const opts = { invalidate: [DAARA_KEY, GOV_KEY], onSuccess: (r: { status: string; notice: string; handoff_digest: string | null }) => setDone({ status: r.status, notice: r.notice, digest: r.handoff_digest }) };
  const dismiss = useCellMutation<void, { status: string; notice: string; handoff_digest: string | null }>((api) => api.daara.decide(p.proposal_id, "dismiss", { reason }), opts);
  const assign = useCellMutation<void, { status: string; notice: string; handoff_digest: string | null }>((api) => api.daara.decide(p.proposal_id, "assign", { assignee }), opts);
  const handoff = useCellMutation<void, { status: string; notice: string; handoff_digest: string | null }>((api) => api.daara.decide(p.proposal_id, "handoff", {}), opts);
  const closed = p.status === "DISMISSED" || p.status === "HANDED_OFF";
  const error = dismiss.error ?? assign.error ?? handoff.error;
  return (
    <Card title="Your decision" description="Each choice records what a person decided. None of them changes access.">
      <div className="space-y-4">
        {closed ? <Banner tone="info" title={PROPOSAL_STATUS[p.status].label}>This proposal is final.</Banner> : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Reason to dismiss" placeholder="Choose a reason…" options={DISMISS_REASONS} value={reason || null} onChange={setReason} isDisabled={closed} />
          <PersonSelect label="Assign to" value={assignee} onChange={setAssignee} description="An active member, not an agent." />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" isDisabled={closed || !reason} pending={dismiss.isPending} onPress={() => dismiss.mutate()}>Dismiss finding</Button>
          <Button variant="secondary" isDisabled={closed || !assignee} pending={assign.isPending} onPress={() => assign.mutate()}>Assign for investigation</Button>
          <Button isDisabled={closed} pending={handoff.isPending} onPress={() => handoff.mutate()}>Open governed access workflow</Button>
        </div>
        <p className="text-xs text-ink-muted">
          &quot;Open governed access workflow&quot; records a hand-off only. The access-change service asks for its own permissions, a fresh policy decision and a person to execute.
        </p>
        <div aria-live="polite">
          {done ? (
            <Banner tone="ok" title={`Recorded: ${PROPOSAL_STATUS[done.status as ReviewProposal["status"]]?.label ?? done.status}`}>
              {done.notice}
              {done.digest ? <span className="mt-1 block break-all font-mono text-2xs">{done.digest}</span> : null}
            </Banner>
          ) : null}
        </div>
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Card>
  );
}

/** One suggestion (WF-AG-04). Rule severity and the basis for it are shown apart, and no text the agent wrote can change either. */
export function ReviewProposalPage({ proposalId }: { proposalId: string }) {
  const proposal = useCellQuery(daaraKey("proposal", proposalId), (a) => a.daara.proposal(proposalId));
  return (
    <>
      <PageHeader
        icon={ClipboardList}
        title="Review suggested access change"
        description="Submitted by an agent, not by a person. You decide; nothing happens to access because you opened this."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Review queue", href: "/admin/agents/review" }, { label: "Suggestion" }]}
      />
      <QueryView query={proposal}>
        {(p) => (
          <div className="space-y-4">
            <Banner tone="info" title="No access permissions will change as a result of this review proposal.">{p.notice}</Banner>
            <Card title={`${p.ref}: ${FINDING_TEXT[p.finding_code] ?? p.finding_code}`}>
              <KeyValues
                columns={2}
                items={[
                  ["Submitted by", "Agent (not a person)"],
                  ["Affected item (masked)", <span key="t" className="font-mono text-xs">{p.target}</span>],
                  ["Rule", `${p.rule_id} v${p.rule_version}: ${p.meaning}`],
                  ["Rule severity", <Badge key="s" tone={SEVERITY[p.severity].tone}>{SEVERITY[p.severity].label}</Badge>],
                  ["Basis", p.confidence_basis === "DETERMINISTIC_RULE" ? "A fixed rule over the facts, not a model's opinion" : p.confidence_basis],
                  ["Seen in runs", `${p.seen_count} time${p.seen_count === 1 ? "" : "s"}`],
                  ["Evidence", p.evidence_refs.join(", ") || "None"],
                  ["Status", PROPOSAL_STATUS[p.status].label],
                ]}
              />
              <p className="mt-3 text-sm">
                <Link className="font-medium text-brand-700 underline" href={`/admin/agents/governance/${p.agent_id}/runs/${p.run_id}`}>Open the run that found it</Link>
              </p>
            </Card>
            <Decision key={p.status} p={p} />
          </div>
        )}
      </QueryView>
    </>
  );
}
