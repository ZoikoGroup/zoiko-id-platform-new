"use client";

import { ApiError, type OwnDecision, type OwnDecisionKind, type OwnFindings, type OwnGraph, type OwnGraphSummary } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, ButtonLink, Card, DataTable, DateTime, EmptyState, LoadingRows, PageHeader, Select } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

export const OWN_KEY = ["ownership"];

const PROBLEMS: Record<string, string> = {
  OWNERSHIP_CYCLE: "The ownership loops back on itself, so shares cannot be worked out. Remove the loop.",
  OWNED_OVER_100_PERCENT: "A company is recorded as more than 100 percent owned.",
  PERCENT_INVALID: "A share must be a decimal string above 0 and at most 100.",
  ENTITY_INVALID: "An entity needs a unique id and one of the fixed kinds.",
  LINK_ENTITY_UNKNOWN: "A link names an entity that is not in the list.",
  SELF_OWNERSHIP: "An entity cannot own itself.",
  LINK_REPEATED: "The same link is recorded twice.",
  PERSON_CANNOT_BE_OWNED: "A person cannot be owned by another entity.",
  TARGET_INVALID: "The target must be a company, trust or other entity, not a person.",
  THRESHOLD_INVALID: "The threshold must be above 0 and at most 100.",
  GRAPH_TOO_LARGE: "The graph has too many entities to review. Split it.",
  REVIEWER_IS_AUTHOR: "You recorded this graph, so someone else decides its candidates.",
  ALREADY_DECIDED: "This candidate already has a decision. A different view needs a new version of the graph.",
  NOT_A_CANDIDATE: "Only a candidate of this version can be decided.",
  REASON_TOO_SHORT: "Give a reason of at least a sentence.",
  EVIDENCE_REF_REQUIRED: "Name the evidence you relied on.",
};

/** A refusal code in words. An unknown code shows as itself. */
export function problemText(code: string): string {
  return PROBLEMS[code.toUpperCase()] ?? code;
}

export const STARTER = `{
  "target": "target-co",
  "threshold": "25",
  "entities": [
    { "id": "target-co", "kind": "COMPANY" },
    { "id": "holdco", "kind": "COMPANY" },
    { "id": "person-1", "kind": "PERSON" },
    { "id": "person-2", "kind": "PERSON" }
  ],
  "links": [
    { "owner": "holdco", "owned": "target-co", "percent": "50", "evidenced": true },
    { "owner": "person-1", "owned": "holdco", "percent": "60", "evidenced": false },
    { "owner": "person-2", "owned": "target-co", "percent": "30", "evidenced": true }
  ]
}
`;

/** One JSON object, refused before it is sent if it is anything else. */
export function parseGraph(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (v === null || typeof v !== "object" || Array.isArray(v)) return { ok: false, error: "A graph is one JSON object." };
    return { ok: true, value: v as Record<string, unknown> };
  } catch (e) {
    return { ok: false, error: `This is not valid JSON: ${e instanceof Error ? e.message : "unreadable"}` };
  }
}

function code(error: unknown): string | null {
  return error instanceof ApiError ? String(error.code ?? "") : null;
}

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  if (error instanceof ApiError && error.status === 422) return <Banner tone="danger" title="This graph cannot be used">{problemText(code(error) ?? "")}</Banner>;
  return <ApiErrorView error={error} />;
}

/** The answer in words: candidates for a person to confirm, and what nobody has explained. It never says anyone IS an owner. */
export function FindingsView({ findings }: { findings: OwnFindings }) {
  return (
    <div className="space-y-4">
      <Banner tone={findings.complete ? "ok" : "warn"} title={findings.complete ? "No material gap" : "There is a gap a person must close"}>
        {findings.complete
          ? "Every part of the company at or above the threshold is explained. A person still confirms each candidate."
          : "Part of the company at or above the threshold is not explained by the recorded links."}{" "}
        Threshold {findings.threshold} percent.
      </Banner>
      <Card title="Candidates" description="People who may need to be confirmed as beneficial owners. A candidate is not a finding.">
        {findings.candidates.length ? (
          <ul className="list-disc ps-5 text-sm">
            {findings.candidates.map((c) => (
              <li key={c.entity_id}>
                <span className="font-mono text-xs">{c.entity_id}</span>: {c.effective_percent} percent through {c.paths} {c.paths === 1 ? "path" : "paths"}
                {c.reasons.includes("CONTROL_BY_OTHER_MEANS") ? ", control by other means" : ""}{" "}
                <Badge tone={c.status === "NEEDS_EVIDENCE" ? "warn" : "info"}>{c.status === "NEEDS_EVIDENCE" ? "Needs evidence" : "Needs confirmation"}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No candidate at this threshold.{findings.fallback ? " A senior managing official is identified by a person instead." : ""}</p>
        )}
      </Card>
      <Card title="Gaps" description="What nobody has explained. A gap is not a zero.">
        {findings.gaps.length ? (
          <ul className="list-disc ps-5 text-sm">
            {findings.gaps.map((g) => (
              <li key={`${g.code}-${g.entity_id}`}>
                <span className="font-mono text-xs">{g.entity_id}</span>: {g.code === "UNRESOLVED_CHAIN" ? "no owners recorded" : "part of it unexplained"}, up to {g.effective_percent} percent of the company
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">No gaps.</p>
        )}
      </Card>
      {findings.notes.map((n) => (
        <p key={n} className="text-sm text-ink-muted">
          {n}
        </p>
      ))}
    </div>
  );
}

export function OwnershipListPage() {
  const list = useCellQuery([...OWN_KEY, "list"], (api) => api.ownership.list());
  return (
    <>
      <PageHeader
        title="Ownership chains"
        description="Who ends up with a material share of a company, and what is not known. The answer is candidates for a person to confirm, never a named owner."
        breadcrumbs={[{ label: "Ownership chains" }]}
        actions={<ButtonLink href="/admin/ownership/new">New graph</ButtonLink>}
      />
      {list.isPending && list.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {list.isError ? <Failed error={list.error} /> : null}
      {list.data ? (
        <DataTable<OwnGraphSummary>
          caption="Recorded ownership graphs"
          rows={list.data.items}
          rowKey={(r) => r.graph_id}
          rowHref={(r) => `/admin/ownership/${encodeURIComponent(r.graph_id)}`}
          empty="No ownership graphs yet."
          columns={[
            { key: "target", header: "Company", pinned: true, render: (r) => <span className="font-medium">{r.target}</span> },
            { key: "version", header: "Version", render: (r) => r.version },
            { key: "hash", header: "Fingerprint", render: (r) => <code className="font-mono text-xs">{r.content_hash.slice(0, 10)}</code> },
            { key: "when", header: "Recorded", render: (r) => <DateTime value={r.created_at} /> },
          ]}
        />
      ) : null}
    </>
  );
}

export function NewOwnershipPage() {
  const [text, setText] = useState(STARTER);
  const [local, setLocal] = useState<string | null>(null);
  const [findings, setFindings] = useState<OwnFindings | null>(null);
  const analyze = useCellMutation((api, body: Record<string, unknown>) => api.ownership.analyze(body as never), { onSuccess: setFindings });
  const record = useCellMutation((api, body: Record<string, unknown>) => api.ownership.record(body as never), { invalidate: [OWN_KEY], success: "Version recorded" });

  function run(action: "analyze" | "record") {
    const parsed = parseGraph(text);
    if (!parsed.ok) {
      setLocal(parsed.error);
      return;
    }
    setLocal(null);
    (action === "analyze" ? analyze : record).mutate(parsed.value);
  }

  const saved = record.data as OwnGraph | undefined;
  return (
    <>
      <PageHeader
        title="New ownership graph"
        description="Entities are ids and a kind, never names. Check as often as you like; recording keeps one version that never changes."
        breadcrumbs={[{ label: "Ownership chains", href: "/admin/ownership" }, { label: "New" }]}
      />
      <div className="space-y-4">
        <label className="block text-sm font-medium text-ink" htmlFor="own-graph">
          Graph
        </label>
        <textarea
          id="own-graph"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          rows={22}
          className="w-full rounded-control border border-border bg-surface p-3 font-mono text-xs text-ink focus-visible:ring-2 focus-visible:ring-brand-500"
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" pending={analyze.isPending} onPress={() => run("analyze")}>
            Work out candidates
          </Button>
          <Button pending={record.isPending} onPress={() => run("record")}>
            Record version
          </Button>
        </div>
        {local ? <Banner tone="danger" title="Not sent">{local}</Banner> : null}
        {analyze.error ? <Failed error={analyze.error} /> : null}
        {record.error ? <Failed error={record.error} /> : null}
        {findings ? <FindingsView findings={findings} /> : null}
        {saved ? (
          <Banner tone="ok" title={`Version ${saved.version} recorded`}>
            <ButtonLink href={`/admin/ownership/${encodeURIComponent(saved.graph_id)}`}>Open it</ButtonLink>
          </Banner>
        ) : null}
      </div>
    </>
  );
}

export const DECISION_TEXT: Record<OwnDecisionKind, string> = {
  CONFIRMED: "Confirmed by a person",
  NOT_CONFIRMED: "Not confirmed by a person",
  ESCALATED: "Escalated",
};

/** The author of a graph cannot decide its candidates: the platform refuses it, and the form says why before it has to. */
export function canDecide(author: string, me: string | undefined, decided: boolean): boolean {
  return !decided && Boolean(me) && me !== author;
}

function DecisionForm({ graphId, entityId, onDone }: { graphId: string; entityId: string; onDone: () => void }) {
  const [decision, setDecision] = useState<OwnDecisionKind>("CONFIRMED");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const decide = useCellMutation((api, body: { entity_id: string; decision: OwnDecisionKind; reason: string; evidence_ref: string }) => api.ownership.decide(graphId, body), {
    invalidate: [OWN_KEY],
    success: "Decision recorded",
    onSuccess: onDone,
  });
  return (
    <div className="mt-2 space-y-2 rounded-control border border-border p-3">
      <Select
        label={`Decision for ${entityId}`}
        options={(Object.keys(DECISION_TEXT) as OwnDecisionKind[]).map((k) => ({ id: k, label: DECISION_TEXT[k] }))}
        value={decision}
        onChange={(v) => setDecision(v as OwnDecisionKind)}
      />
      <label className="block text-sm text-ink" htmlFor={`own-reason-${entityId}`}>
        Reason
      </label>
      <textarea id={`own-reason-${entityId}`} value={reason} onChange={(e) => setReason(e.target.value)} rows={3} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
      <label className="block text-sm text-ink" htmlFor={`own-evidence-${entityId}`}>
        Evidence reference
      </label>
      <input id={`own-evidence-${entityId}`} value={evidence} onChange={(e) => setEvidence(e.target.value)} className="w-full rounded-control border border-border bg-surface p-2 text-sm text-ink" />
      <Button pending={decide.isPending} onPress={() => decide.mutate({ entity_id: entityId, decision, reason, evidence_ref: evidence })}>
        Record my decision
      </Button>
      {decide.error ? <Failed error={decide.error} /> : null}
    </div>
  );
}

function Decisions({ graph, me, refetch }: { graph: OwnGraph; me?: string; refetch: () => void }) {
  const byEntity = new Map<string, OwnDecision>(graph.confirmations.map((c) => [c.entity_id, c]));
  const candidates = graph.findings?.candidates ?? [];
  if (!candidates.length) return null;
  return (
    <Card title="Decisions" description="A person decides on each candidate, with a reason and the evidence they relied on. The platform never decides, and the person who recorded the graph does not.">
      <ul className="space-y-3 text-sm">
        {candidates.map((c) => {
          const d = byEntity.get(c.entity_id);
          return (
            <li key={c.entity_id}>
              <span className="font-mono text-xs">{c.entity_id}</span>:{" "}
              {d ? (
                <>
                  <Badge tone={d.decision === "CONFIRMED" ? "ok" : "warn"}>{DECISION_TEXT[d.decision]}</Badge> ({d.evidence_ref})
                </>
              ) : (
                <Badge tone="neutral">No decision yet</Badge>
              )}
              {!d && me && me === graph.author ? <p className="mt-1 text-ink-muted">You recorded this graph, so someone else decides.</p> : null}
              {canDecide(graph.author, me, Boolean(d)) ? <DecisionForm graphId={graph.graph_id} entityId={c.entity_id} onDone={refetch} /> : null}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export function OwnershipDetailPage({ graphId, me }: { graphId: string; me?: string }) {
  const query = useCellQuery([...OWN_KEY, "graph", graphId], (api) => api.ownership.graph(graphId));
  const g = query.data;
  return (
    <>
      <PageHeader
        title={g ? `${g.target} version ${g.version}` : "Ownership graph"}
        description="A recorded version never changes. The candidates and gaps are worked out again from it each time."
        breadcrumbs={[{ label: "Ownership chains", href: "/admin/ownership" }, { label: g ? `${g.target} v${g.version}` : "Graph" }]}
      />
      {query.isPending && query.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {query.isError ? <Failed error={query.error} /> : null}
      {g?.findings ? <FindingsView findings={g.findings} /> : null}
      {g ? <Decisions graph={g} me={me} refetch={() => void query.refetch()} /> : null}
      {g && !g.findings ? <Banner tone="warn" title="Findings are not available">{problemText(g.findings_error ?? "")}</Banner> : null}
    </>
  );
}
