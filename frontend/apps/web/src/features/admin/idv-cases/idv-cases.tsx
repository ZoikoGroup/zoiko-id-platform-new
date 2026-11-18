"use client";

import { ApiError, type IdvCase } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, Card, DataTable, DateTime, EmptyState, LoadingRows, PageHeader } from "@zoiko/ui";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { canReview, claimText, DECISION, explainReason, IDV_KEY, problemText, SIGNAL_LABEL, signalWords } from "./logic";
import { ReviewerPanel } from "./reviewer-panel";
import { ReviewQueue } from "./review-queue";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    const text = problemText(String(error.code ?? ""));
    if (text !== String(error.code ?? "")) return <Banner tone="danger" title="Not allowed">{text}</Banner>;
    return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  if (error instanceof ApiError && [409, 422].includes(error.status)) return <Banner tone="danger" title="This could not be done">{problemText(String(error.code ?? ""))}</Banner>;
  return <ApiErrorView error={error} />;
}

function Detail({ c }: { c: IdvCase }) {
  const review = useCellMutation((api, id: string) => api.idv.review(id), { invalidate: [IDV_KEY], success: "Your signature is recorded" });
  return (
    <div className="space-y-3 text-sm">
      <p>{DECISION[c.decision].meaning}</p>
      <ul className="list-disc pl-5">
        {c.reasons.map((r) => (
          <li key={r}>{explainReason(r)}</li>
        ))}
      </ul>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        {Object.entries(c.signals).map(([k, v]) => (
          <div key={k}>
            <dt className="text-muted">{SIGNAL_LABEL[k] ?? k}</dt>
            <dd>{signalWords(v)}</dd>
          </div>
        ))}
      </dl>
      {c.assertions.length ? (
        <p>
          <strong>What may be said:</strong> {c.assertions.map(claimText).join("; ")}.
        </p>
      ) : (
        <p>No claim may be made from this case yet.</p>
      )}
      <p className="text-muted">
        Decided under the market's rules {c.policy_version} (digest {c.policy_hash.slice(0, 15)}…).
      </p>
      {canReview(c) ? (
        <>
          <ReviewerPanel caseId={c.case_id} />
          <Button pending={review.isPending} onPress={() => review.mutate(c.case_id)}>
            Sign as a reviewer ({c.reviewers} so far)
          </Button>
          {review.error ? <Failed error={review.error} /> : null}
        </>
      ) : null}
    </div>
  );
}

export function IdvCasesPage() {
  const q = useCellQuery(IDV_KEY, (api) => api.idv.cases());
  return (
    <div className="space-y-6">
      <PageHeader title="Identity proofing cases" description="Each case keeps its evidence as separate signals and one decision under the market's rules. Missing evidence is never read as a pass, and a chip that was read is not a verified chip." />
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={4} /> : null}
      {q.isError ? <Failed error={q.error} /> : null}
      <ReviewQueue />
      <Card title="Cases">
        <DataTable<IdvCase>
          caption="Identity proofing cases"
          rows={q.data?.items ?? []}
          rowKey={(r) => r.case_id}
          empty="No identity proofing cases yet."
          columns={[
            { key: "m", header: "Market", pinned: true, render: (r) => `${r.market} · ${r.route === "CHIP" ? "chip" : r.route.toLowerCase().replace(/_/g, " ")}` },
            { key: "d", header: "Decision", render: (r) => <Badge tone={DECISION[r.decision].tone}>{r.decision.toLowerCase().replace(/_/g, " ")}</Badge> },
            { key: "u", header: "Updated", render: (r) => <DateTime value={r.updated_at} /> },
            { key: "x", header: "Why", render: (r) => <Detail c={r} /> },
          ]}
        />
      </Card>
    </div>
  );
}
