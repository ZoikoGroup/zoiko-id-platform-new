"use client";

import type { BreakGlassReview } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ClipboardCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

const REVIEWS_KEY = ["operator", "reviews"];

export function ReviewsPage() {
  const [state, setState] = useState("REVIEW_PENDING");
  const reviews = useCellQuery([...REVIEWS_KEY, state], (api) => api.reviews(state));
  const [reviewing, setReviewing] = useState<BreakGlassReview | null>(null);
  const [outcome, setOutcome] = useState<"JUSTIFIED" | "UNJUSTIFIED">("JUSTIFIED");
  const [notes, setNotes] = useState("");
  const complete = useCellMutation((api, r: BreakGlassReview) => api.completeReview(r.review_id, { outcome, notes: notes.trim() }), {
    invalidate: [REVIEWS_KEY],
    success: (r) => (r.outcome === "UNJUSTIFIED" ? "Recorded as unjustified: routed to the security incident process" : "Review completed"),
    onSuccess: () => {
      setReviewing(null);
      setNotes("");
    },
  });
  const columns: Column<BreakGlassReview>[] = [
    { key: "grant", header: "Break-glass grant", pinned: true, render: (r) => <CopyId value={r.jit_grant_id} label="grant ID" /> },
    { key: "requester", header: "Operator", render: (r) => <span className="font-mono text-2xs">{r.requester_principal_id}</span> },
    { key: "state", header: "State", render: (r) => <StatusBadge state={r.state} kind="Post-review" /> },
    { key: "outcome", header: "Outcome", render: (r) => (r.outcome ? <StatusBadge state={r.outcome} kind="Review outcome" /> : "—") },
    { key: "created", header: "Opened", render: (r) => <DateTime value={r.created_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.state === "REVIEW_PENDING" ? (
          <Button size="xs" onPress={() => setReviewing(r)}>
            Review
          </Button>
        ) : null,
    },
  ];
  return (
    <>
      <PageHeader title="Post-reviews" description="Every break-glass is reviewed by someone other than the operator who used it." />
      <Card
        padded={false}
        title="Break-glass reviews"
        actions={
          <Select
            label="State"
            hideLabel
            value={state}
            onChange={setState}
            options={[
              { id: "REVIEW_PENDING", label: "Pending" },
              { id: "REVIEWED", label: "Reviewed" },
            ]}
          />
        }
      >
        <QueryView query={reviews}>
          {(d) => <DataTable caption="Reviews" columns={columns} rows={d.items} rowKey={(r) => r.review_id} empty={<EmptyState icon={ClipboardCheck} title="No reviews in this state" />} />}
        </QueryView>
      </Card>
      <Dialog
        isOpen={reviewing !== null}
        onOpenChange={(o) => !o && setReviewing(null)}
        title="Complete the post-review"
        footer={
          <>
            <Button variant="secondary" onPress={() => setReviewing(null)}>
              Cancel
            </Button>
            <Button pending={complete.isPending} isDisabled={!notes.trim()} onPress={() => reviewing && complete.mutate(reviewing)}>
              Record review
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select
            label="Outcome"
            value={outcome}
            onChange={(v) => setOutcome(v === "UNJUSTIFIED" ? "UNJUSTIFIED" : "JUSTIFIED")}
            options={[
              { id: "JUSTIFIED", label: "Justified", description: "The emergency warranted the access taken" },
              { id: "UNJUSTIFIED", label: "Unjustified", description: "A finding: goes to the security incident process" },
            ]}
          />
          <TextField label="Review notes" value={notes} onChange={setNotes} isRequired multiline rows={4} />
          {complete.error ? <ApiErrorView error={complete.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
