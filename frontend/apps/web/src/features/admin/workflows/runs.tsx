"use client";

import type { WfRun, WfRunSummary } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, Card, DataTable, DateTime, EmptyState, LoadingRows, PageHeader, Select } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { useMe } from "../authority/shared";
import { OUTCOMES, WF_KEY, classify } from "./logic";

export const RUN_STATUS: Record<WfRun["status"], { label: string; tone: "neutral" | "ok" | "warn" | "danger" | "info" }> = {
  WAITING_FOR_CHECK: { label: "Waiting for a check", tone: "info" },
  WAITING_FOR_REVIEW: { label: "Waiting for a person", tone: "warn" },
  ENDED: { label: "Ended", tone: "neutral" },
};

/** What a run is waiting for, in words. */
export function waitingText(r: Pick<WfRun, "status" | "awaiting_check" | "awaiting_role">): string {
  if (r.status === "WAITING_FOR_CHECK") return `The ${(r.awaiting_check ?? "next").replaceAll("_", " ").toLowerCase()} result`;
  if (r.status === "WAITING_FOR_REVIEW") return `A ${(r.awaiting_role ?? "reviewer").replaceAll("_", " ")} to look`;
  return "Nothing";
}

/** The applicant cannot review their own run: the platform refuses it, and the button says why before it has to. */
export function canReview(run: Pick<WfRun, "status" | "subject_principal_id">, me: string | undefined): boolean {
  return run.status === "WAITING_FOR_REVIEW" && Boolean(me) && me !== run.subject_principal_id;
}

function Failed({ error }: { error: unknown }) {
  const kind = classify(error);
  if (kind === "forbidden") return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  if (kind === "missing") return <AccessUnavailable correlationId={(error as { correlationId?: string }).correlationId} />;
  return <ApiErrorView error={error} />;
}

export function WorkflowRunsPage() {
  const runs = useCellQuery([...WF_KEY, "runs"], (api) => api.workflows.runs());
  return (
    <>
      <PageHeader
        title="Workflow runs"
        description="Each run follows an approved workflow for one person. A run only moves on a result it is waiting for or a person's review; it never declines anyone."
        breadcrumbs={[{ label: "Workflows", href: "/admin/workflows" }, { label: "Runs" }]}
      />
      {runs.isPending && runs.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {runs.isError ? <Failed error={runs.error} /> : null}
      {runs.data ? (
        <DataTable<WfRunSummary>
          caption="Workflow runs"
          rows={runs.data.items}
          rowKey={(r) => r.run_id}
          rowHref={(r) => `/admin/workflows/runs/${encodeURIComponent(r.run_id)}`}
          empty="No runs yet."
          columns={[
            { key: "run", header: "Run", pinned: true, render: (r) => <span className="font-mono text-xs">{r.run_id.slice(0, 8)}</span> },
            { key: "wf", header: "Workflow", render: (r) => `${r.workflow_id} ${r.version}` },
            { key: "status", header: "Status", render: (r) => <Badge tone={RUN_STATUS[r.status].tone}>{RUN_STATUS[r.status].label}</Badge> },
            { key: "outcome", header: "Outcome", render: (r) => (r.outcome ? r.outcome.replaceAll("_", " ").toLowerCase() : "Not ended") },
            { key: "when", header: "Started", render: (r) => <DateTime value={r.created_at} /> },
          ]}
        />
      ) : null}
    </>
  );
}

export function WorkflowRunPage({ runId, me }: { runId: string; me?: string }) {
  const key = [...WF_KEY, "run", runId];
  const query = useCellQuery(key, (api) => api.workflows.run(runId));
  const [outcome, setOutcome] = useState("VERIFIED");
  const report = useCellMutation((api, args: { check: string; outcome: string }) => api.workflows.reportCheck(runId, args.check, args.outcome), {
    invalidate: [WF_KEY],
    success: "Result recorded",
    onSuccess: () => void query.refetch(),
  });
  const review = useCellMutation((api) => api.workflows.completeReview(runId), { invalidate: [WF_KEY], success: "Review recorded", onSuccess: () => void query.refetch() });
  const run = query.data;
  return (
    <>
      <PageHeader
        title={`Run ${runId.slice(0, 8)}`}
        description="A run follows its workflow's steps. Nothing on this page decides about the person: a result or a person's review moves it."
        breadcrumbs={[{ label: "Workflows", href: "/admin/workflows" }, { label: "Runs", href: "/admin/workflows/runs" }, { label: runId.slice(0, 8) }]}
      />
      {query.isPending && query.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {query.isError ? <Failed error={query.error} /> : null}
      {run ? (
        <div className="space-y-4">
          <Banner tone={run.status === "ENDED" ? "ok" : "info"} title={RUN_STATUS[run.status].label}>
            {run.status === "ENDED" ? `Ended: ${(run.outcome ?? "").replaceAll("_", " ").toLowerCase()}.` : `Waiting for: ${waitingText(run)}.`} Workflow {run.workflow_id} {run.version}.
          </Banner>

          {run.status === "WAITING_FOR_CHECK" && run.awaiting_check ? (
            <Card title="Record a check result" description="The result a check service gave. A result that is not Verified never approves anyone.">
              <div className="space-y-3">
                <Select label={run.awaiting_check.replaceAll("_", " ").toLowerCase()} options={OUTCOMES.map((o) => ({ id: o, label: o.replaceAll("_", " ").toLowerCase() }))} value={outcome} onChange={setOutcome} />
                <Button pending={report.isPending} onPress={() => report.mutate({ check: run.awaiting_check as string, outcome })}>
                  Record result
                </Button>
                {report.error ? <Failed error={report.error} /> : null}
              </div>
            </Card>
          ) : null}

          {run.status === "WAITING_FOR_REVIEW" ? (
            <Card title="Review" description="A person looks at this case and records that they did. The applicant cannot review their own run.">
              <div className="space-y-3">
                <Button pending={review.isPending} isDisabled={!canReview(run, me)} onPress={() => review.mutate(undefined)}>
                  Record that I reviewed this
                </Button>
                {me && me === run.subject_principal_id ? <p className="text-sm text-ink-muted">This run is about you, so someone else reviews it.</p> : null}
                {review.error ? <Failed error={review.error} /> : null}
              </div>
            </Card>
          ) : null}

          <Card title="Results so far" description="Outcome words only: no value from the person is kept here.">
            {run.erased ? (
              <p className="text-sm text-ink-muted">The results were erased under a data-rights request. The outcome stays.</p>
            ) : Object.keys(run.results).length ? (
              <ul className="list-disc ps-5 text-sm">
                {Object.entries(run.results).map(([check, result]) => (
                  <li key={check}>
                    {check.replaceAll("_", " ").toLowerCase()}: {result.replaceAll("_", " ").toLowerCase()}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">No results yet.</p>
            )}
          </Card>

          <Card title="Trace" description="What happened, step by step.">
            <ol className="list-decimal ps-5 text-sm">
              {run.events.map((e, i) => (
                <li key={`${i}-${e.step}-${e.kind}`}>
                  {e.step}: {e.kind.replaceAll("_", " ").toLowerCase()}
                  {e.detail ? ` (${e.detail})` : ""}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      ) : null}
    </>
  );
}

export function WorkflowRunRoute({ runId }: { runId: string }) {
  return <WorkflowRunPage runId={runId} me={useMe()} />;
}
