"use client";

import type { ApprovalAwaiting } from "@zoiko/api-client";
import { Banner, Button, Card, DateTime, EmptyState } from "@zoiko/ui";
import { CheckCircle2, Clock, Stamp } from "lucide-react";
import type { ReactNode } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

const WAITING_KEY = ["approvals", "awaiting-me"];
const changeKey = (a: string, r: string, c: string) => ["approvals", "for-change", a, r, c];

/** Why a second person is involved, in one place so every screen says it the same way. */
export const TWO_PERSON_RULE = "Changes like this need a second administrator, so no one person can make them alone.";

/**
 * Asks another administrator to approve one exact change, and follows the answer.
 *
 * The person never sees an identifier or a context string: the request is opened for this action, resource and change,
 * found again after a reload, and handed to `children(requestId)` once approved. Anything the person edits after asking
 * is a different change, so it needs its own approval.
 */
export function GuidedApproval({
  action,
  resource,
  change,
  summary,
  label,
  children,
}: {
  action: string;
  resource: string;
  change: string;
  /** What is being approved, in words: this is what the approver reads. */
  summary: string;
  /** The verb on the step, e.g. "Approve plan". */
  label: string;
  /** Rendered once approved, with the approved request's id. */
  children: (requestId: string) => ReactNode;
}) {
  const key = changeKey(action, resource, change);
  const found = useCellQuery(key, (api) => api.authority.approvalForChange({ action, resource, change }), { refetchInterval: 5_000 });
  const ask = useCellMutation(
    (api) =>
      api.authority.createApproval({
        subject_ref: resource,
        action,
        resource,
        change,
        summary: summary.slice(0, 300),
        mode: "SINGLE",
        roles: [action],
        k: 1,
        ttl_seconds: 86400,
      }),
    { success: "Asked another administrator to approve", invalidate: [key] },
  );

  const req = found.data?.request ?? null;
  const state = req?.state;

  return (
    <div className="space-y-3 rounded-card border border-border p-4">
      <div className="flex items-start gap-2">
        <Stamp aria-hidden className="mt-0.5 size-4 shrink-0 text-brand-700" />
        <div className="text-sm text-ink-muted">
          <strong className="text-ink">{label}</strong> needs a second administrator. {TWO_PERSON_RULE}
          <div className="mt-1 rounded-md bg-sunken px-3 py-2 text-ink">{summary}</div>
        </div>
      </div>

      {state === "APPROVED" && req ? (
        <div className="space-y-3">
          <Banner tone="ok" icon={CheckCircle2} title="Approved by another administrator">
            You can go ahead. If you change anything above first, ask again: an approval covers exactly what was shown.
          </Banner>
          {children(req.approval_request_id)}
        </div>
      ) : state === "PENDING" && req ? (
        <Banner tone="info" icon={Clock} title="Waiting for another administrator">
          Asked <DateTime value={req.created_at} />. They see it under <strong>Waiting for your approval</strong> on their Overview and in
          Authority &amp; approvals. This page updates by itself.
        </Banner>
      ) : (
        <div className="space-y-3">
          {state === "DENIED" ? (
            <Banner tone="warn" title="Declined">
              Another administrator declined this. Ask again if the change is still needed.
            </Banner>
          ) : null}
          {state === "EXPIRED" || state === "STALE" ? (
            <Banner tone="info" title={state === "EXPIRED" ? "The approval expired" : "The change was edited after it was approved"}>
              Ask again so the approver sees what is there now.
            </Banner>
          ) : null}
          <Button variant="secondary" size="sm" pending={ask.isPending || found.isLoading} onPress={() => ask.mutate(undefined)}>
            Ask another administrator to approve
          </Button>
        </div>
      )}
      {ask.error ? <ApiErrorView error={ask.error} title="The request was not sent" /> : null}
    </div>
  );
}

/** What the signed-in administrator is being asked to approve, one click each. Nothing is shown when nothing waits. */
export function ApprovalsWaiting({ compact = false }: { compact?: boolean }) {
  const waiting = useCellQuery(WAITING_KEY, (api) => api.authority.approvalsAwaitingMe(), { refetchInterval: 10_000 });
  const decide = useCellMutation(
    (api, v: { id: string; verdict: "APPROVE" | "DENY" }) => api.authority.decideApproval(v.id, { verdict: v.verdict, risk_class: 3 }),
    {
      success: (r) => (r.state === "APPROVED" ? "Approved" : "Your decision was recorded"),
      invalidate: [WAITING_KEY, ["approvals"]],
    },
  );
  const items = waiting.data?.items ?? [];
  if (waiting.isError || (compact && items.length === 0)) return null;
  return (
    <Card
      title="Waiting for your approval"
      description="Changes another administrator asked for. Read what each one does before you decide."
      padded={items.length > 0}
    >
      {items.length === 0 ? (
        <EmptyState icon={Stamp} title="Nothing is waiting for you" compact>
          When another administrator asks for a change that needs a second person, it appears here.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((a: ApprovalAwaiting) => (
            <li key={a.approval_request_id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{a.summary ?? a.subject_ref}</p>
                <p className="mt-0.5 text-xs text-ink-muted">
                  Asked <DateTime value={a.created_at} /> · expires <DateTime value={a.expires_at} />
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={decide.isPending}
                  onPress={() => decide.mutate({ id: a.approval_request_id, verdict: "DENY" })}
                >
                  Decline
                </Button>
                <Button size="sm" pending={decide.isPending} onPress={() => decide.mutate({ id: a.approval_request_id, verdict: "APPROVE" })}>
                  Approve
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {decide.error ? <ApiErrorView error={decide.error} title="Your decision was not recorded" /> : null}
    </Card>
  );
}
