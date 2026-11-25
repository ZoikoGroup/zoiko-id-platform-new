"use client";

import type { VerifyApiCaseOperator } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, TextField } from "@zoiko/ui";
import { Scale } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const KEY = [...COMMERCIAL_KEY, "verify-cases"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");

export const KIND_TEXT: Record<string, string> = {
  DISPUTE: "Dispute: the company disagrees with a recorded result",
  APPEAL: "Appeal: the company asks for a person to look again at a result that was not verified",
  ERASURE: "Erasure: the company asks for a customer reference to be removed from its sessions",
};

/** A resolution needs a reason the company can read; the same goes for a rejection. */
export const noteReady = (note: string): boolean => note.trim().length >= 3;

/** Disputes, appeals and erasure requests from Verify API partners (CR-159). One person takes a case into review; a DIFFERENT person resolves or rejects it. A dispute or appeal never changes a result. Resolving an
 * erasure removes the customer reference from the company's sessions at once and keeps the results. */
export function VerifyCasesView() {
  const list = useCellQuery(KEY, (api) => api.commercial.verifyCases());
  return (
    <Card title="Verify API cases" description="Disputes, appeals and erasure requests from partners. Oldest first. The person who takes a case into review cannot resolve it.">
      {list.isPending ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : list.error ? (
        <ApiErrorView error={list.error} title="The cases could not be loaded" onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="Nothing waiting" icon={Scale} compact>
          No partner has an open case.
        </EmptyState>
      ) : (
        <div className="space-y-4">
          {list.data.items.map((c) => (
            <CasePanel key={c.case_id} c={c} />
          ))}
        </div>
      )}
    </Card>
  );
}

function CasePanel({ c }: { c: VerifyApiCaseOperator }) {
  const [note, setNote] = useState("");
  const opts = { invalidate: [KEY] };
  const review = useCellMutation<void, unknown>((api) => api.commercial.verifyCaseReview(c.case_id), { ...opts, success: "Taken into review" });
  const resolve = useCellMutation<void, unknown>((api) => api.commercial.verifyCaseResolve(c.case_id, note), {
    ...opts,
    success: c.kind === "ERASURE" ? "Resolved. The customer reference was removed from the company's sessions." : "Resolved. The company reads the note.",
  });
  const reject = useCellMutation<void, unknown>((api) => api.commercial.verifyCaseReject(c.case_id, note), { ...opts, success: "Not upheld. The company reads the reason." });
  const busy = review.isPending || resolve.isPending || reject.isPending;
  const error = review.error ?? resolve.error ?? reject.error;
  return (
    <section aria-label={`Case from ${c.tenant_id}`} className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{c.tenant_id}</span>
        <Badge tone={c.status === "IN_REVIEW" ? "warn" : "info"}>{c.status === "IN_REVIEW" ? "In review" : "Waiting"}</Badge>
        <span className="text-xs text-ink-muted">Sent {when(c.created_at)}</span>
      </div>
      <p className="text-sm font-medium">{KIND_TEXT[c.kind] ?? c.kind}</p>
      {c.session_id ? <p className="font-mono text-xs">{c.session_id}</p> : <p className="text-xs text-ink-muted">The customer reference is held only as a fingerprint and is not shown.</p>}
      <div>
        <p className="text-xs text-ink-muted">In their words</p>
        <p className="whitespace-pre-wrap text-sm">{c.reason}</p>
      </div>
      {c.status === "OPEN" ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" isDisabled={busy} onPress={() => review.mutate(undefined)}>
            Take into review
          </Button>
          <Button size="sm" variant="secondary" isDisabled={busy || !noteReady(note)} onPress={() => reject.mutate(undefined)}>
            Reject without review
          </Button>
          <TextField label="Reason (the company reads it)" value={note} onChange={setNote} />
        </div>
      ) : (
        <div className="space-y-3">
          {c.kind === "ERASURE" ? (
            <Banner tone="warn" title="Resolving this erases data">
              The customer reference is removed from every session of this company that carries it, and the free-form details with it. Results stay. This cannot be undone.
            </Banner>
          ) : null}
          <TextField label="Note (the company reads it)" value={note} onChange={setNote} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" isDisabled={busy || !noteReady(note)} pending={resolve.isPending} onPress={() => resolve.mutate(undefined)}>
              {c.kind === "ERASURE" ? "Resolve and erase" : "Resolve"}
            </Button>
            <Button size="sm" variant="secondary" isDisabled={busy || !noteReady(note)} onPress={() => reject.mutate(undefined)}>
              Reject
            </Button>
          </div>
        </div>
      )}
      {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (review.reset(), resolve.reset(), reject.reset())} /> : null}
    </section>
  );
}
