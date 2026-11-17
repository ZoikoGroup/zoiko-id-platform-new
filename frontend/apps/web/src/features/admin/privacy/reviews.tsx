"use client";

import type { DecisionReview, ReviewState } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Scale } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { PRIVACY_KEY } from "./shared";

/** Appeals and human review (ZID-DTV-WF-04 B08; ZID-PRI-004 sections 10 and 11; CR-109). The person who asked never reviews their own case.
 * Upholding a decision closes the review. Overturning or correcting it is only a proposal until a third person, neither the requester nor the
 * reviewer, confirms it; then whoever relied on the decision is told it changed. The person's statement and the rationale stay on the record and
 * are never put in an event. No face image, template or score is shown here. */

const REVIEW_KEY = [...PRIVACY_KEY, "reviews"];
const FILTERS: { id: ReviewState | "ALL"; label: string }[] = [
  { id: "OPEN", label: "Waiting for a reviewer" },
  { id: "AWAITING_CHECKER", label: "Waiting for a checker" },
  { id: "ALL", label: "All" },
];

export const REVIEW_ERROR_HELP: Record<string, string> = {
  REVIEWER_IS_REQUESTER: "You asked for this review, so you cannot decide it.",
  CHECKER_IS_REQUESTER: "You asked for this review, so you cannot check it.",
  CHECKER_IS_REVIEWER: "You proposed this outcome. A different person checks it.",
  CORRECTION_REF_REQUIRED: "A correction names the corrected record.",
  REVIEW_NOT_AWAITING_CHECKER: "This review is not waiting for a checker any more. Reload.",
  PRIVACY_INVALID_STATE: "This review was already decided. Reload.",
};

function Err({ error }: { error: unknown }) {
  const code = (error as { code?: string } | null)?.code;
  if (code && REVIEW_ERROR_HELP[code]) {
    return (
      <Banner tone="danger" title="Not done" live="assertive">
        {REVIEW_ERROR_HELP[code]}
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

/** `now` is when the list was fetched, so the badge is pure and matches what the reviewer is looking at. */
function Overdue({ r, now }: { r: DecisionReview; now: number }) {
  const open = r.state === "OPEN" || r.state === "AWAITING_CHECKER";
  if (!open) return <DateTime value={r.due_at} style="date" />;
  const late = new Date(r.due_at).getTime() < now;
  return (
    <span className="inline-flex items-center gap-1.5">
      <DateTime value={r.due_at} style="date" />
      {late ? <Badge tone="danger">Overdue</Badge> : null}
    </span>
  );
}

/* --------------------------------------------------------------------------------------------------------------------------- decide */

function DecideDialog({ r, onClose }: { r: DecisionReview | null; onClose: () => void }) {
  const [outcome, setOutcome] = useState<"UPHELD" | "OVERTURNED" | "CORRECTED">("UPHELD");
  const [rationale, setRationale] = useState("");
  const [correction, setCorrection] = useState("");
  const decide = useCellMutation(
    (api, x: DecisionReview) => api.privacy.v2.decideReview(x.review_id, { outcome, rationale: rationale.trim(), correction_ref: outcome === "CORRECTED" ? correction.trim() : null }),
    {
      invalidate: [REVIEW_KEY],
      success: (res) => (res.state === "AWAITING_CHECKER" ? "Proposed: a third person must confirm it" : "Decision upheld; the person is told"),
      onSuccess: () => {
        setRationale("");
        setCorrection("");
        setOutcome("UPHELD");
        onClose();
      },
    },
  );
  const reversal = outcome !== "UPHELD";
  const ready = rationale.trim().length >= 3 && (outcome !== "CORRECTED" || correction.trim().length > 0);
  return (
    <Dialog
      isOpen={r !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Review this decision"
      description="Look at the person's statement and the evidence on record. Never decide a review you asked for."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={decide.isPending} isDisabled={!ready} onPress={() => r && decide.mutate(r)}>
            {reversal ? "Propose" : "Uphold"}
          </Button>
        </>
      }
    >
      {r ? (
        <div className="space-y-4">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="font-medium">Decision</dt>
              <dd className="font-mono text-xs">{r.decision_ref}</dd>
            </div>
            <div>
              <dt className="font-medium">Asked for</dt>
              <dd>{r.kind === "APPEAL" ? "An appeal" : "A human review"}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="font-medium">What the person said</dt>
              <dd className="whitespace-pre-wrap">{r.statement}</dd>
            </div>
          </dl>
          <Select
            label="Outcome"
            value={outcome}
            onChange={(v) => setOutcome(v === "OVERTURNED" ? "OVERTURNED" : v === "CORRECTED" ? "CORRECTED" : "UPHELD")}
            options={[
              { id: "UPHELD", label: "Uphold the decision" },
              { id: "OVERTURNED", label: "Overturn it (needs a checker)" },
              { id: "CORRECTED", label: "Correct it (needs a checker)" },
            ]}
          />
          {outcome === "CORRECTED" ? <TextField label="Corrected record" description="the reference of the corrected decision or record" value={correction} onChange={setCorrection} isRequired /> : null}
          <TextField label="Rationale" description="kept on the record; never put in an event" value={rationale} onChange={setRationale} multiline rows={3} isRequired />
          {reversal ? <Banner tone="info">A reversal takes effect only after a third person, not you and not the person who asked, confirms it.</Banner> : null}
          {decide.error ? <Err error={decide.error} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/* --------------------------------------------------------------------------------------------------------------------------- check */

function CheckDialog({ r, onClose }: { r: DecisionReview | null; onClose: () => void }) {
  const [note, setNote] = useState("");
  const check = useCellMutation((api, v: { x: DecisionReview; confirm: boolean }) => api.privacy.v2.checkReview(v.x.review_id, { confirm: v.confirm, note: note.trim() }), {
    invalidate: [REVIEW_KEY],
    success: (res) => (res.state === "OPEN" ? "Sent back for another review" : "Confirmed: the decision changed and whoever relied on it is told"),
    onSuccess: () => {
      setNote("");
      onClose();
    },
  });
  return (
    <Dialog
      isOpen={r !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Check a proposed reversal"
      description="You must be neither the person who asked nor the reviewer."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="secondary" pending={check.isPending} isDisabled={note.trim().length < 3} onPress={() => r && check.mutate({ x: r, confirm: false })}>
            Send back
          </Button>
          <Button pending={check.isPending} isDisabled={note.trim().length < 3} onPress={() => r && check.mutate({ x: r, confirm: true })}>
            Confirm
          </Button>
        </>
      }
    >
      {r ? (
        <div className="space-y-4">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="font-medium">Decision</dt>
              <dd className="font-mono text-xs">{r.decision_ref}</dd>
            </div>
            <div>
              <dt className="font-medium">Proposed</dt>
              <dd>
                <StatusBadge state={r.proposed_outcome} kind="Proposed outcome" />
                {r.correction_ref ? <span className="ml-2 font-mono text-xs">{r.correction_ref}</span> : null}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="font-medium">What the person said</dt>
              <dd className="whitespace-pre-wrap">{r.statement}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="font-medium">The reviewer&apos;s rationale</dt>
              <dd className="whitespace-pre-wrap">{r.rationale}</dd>
            </div>
          </dl>
          <TextField label="Your note" description="why you confirm or send it back" value={note} onChange={setNote} multiline rows={2} isRequired />
          {check.error ? <Err error={check.error} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/* --------------------------------------------------------------------------------------------------------------------------- list */

export function ReviewsSection() {
  const { session } = useRuntime();
  const me = session?.authenticated ? session.subject.sub : undefined; // a display hint only: the server refuses the requester and the reviewer anyway
  const [filter, setFilter] = useState<ReviewState | "ALL">("OPEN");
  const q = useCellQuery([...REVIEW_KEY, filter], (api) => api.privacy.v2.reviews(filter === "ALL" ? undefined : filter));
  const [deciding, setDeciding] = useState<DecisionReview | null>(null);
  const [checking, setChecking] = useState<DecisionReview | null>(null);
  const cols: Column<DecisionReview>[] = [
    { key: "due", header: "Due", pinned: true, render: (r) => <Overdue r={r} now={q.dataUpdatedAt} /> },
    { key: "kind", header: "Asked for", render: (r) => (r.kind === "APPEAL" ? "Appeal" : "Human review") },
    { key: "decision", header: "Decision", render: (r) => <span className="font-mono text-xs">{r.decision_ref}</span> },
    { key: "state", header: "State", render: (r) => <StatusBadge state={r.state} kind="Review" /> },
    { key: "statement", header: "Statement", render: (r) => <span className="line-clamp-2 text-xs">{r.statement}</span> },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (r) => {
        const mine = me !== undefined && r.requested_by === me;
        if (r.state === "OPEN") {
          return mine ? (
            <Badge tone="neutral">You asked</Badge>
          ) : (
            <Button size="xs" variant="secondary" onPress={() => setDeciding(r)} aria-label={`Review ${r.decision_ref}`}>
              Review
            </Button>
          );
        }
        if (r.state === "AWAITING_CHECKER") {
          return mine || r.reviewer === me ? (
            <Badge tone="neutral">{mine ? "You asked" : "You reviewed"}</Badge>
          ) : (
            <Button size="xs" onPress={() => setChecking(r)} aria-label={`Check ${r.decision_ref}`}>
              Check
            </Button>
          );
        }
        return null;
      },
    },
  ];
  return (
    <div className="space-y-5">
      <Card
        padded={false}
        title="Appeals and human reviews"
        description="People contesting a decision about them. Asking never counts against them. A reversal takes effect only after a third person confirms it."
        actions={<Select label="Show" value={filter} onChange={(v) => setFilter((FILTERS.find((f) => f.id === v)?.id ?? "OPEN") as ReviewState | "ALL")} options={FILTERS} />}
      >
        <QueryView query={q}>
          {(d) => <DataTable caption="Reviews" columns={cols} rows={d.items} rowKey={(r) => r.review_id} empty={<EmptyState icon={Scale} title="Nothing to review" />} />}
        </QueryView>
      </Card>
      <DecideDialog r={deciding} onClose={() => setDeciding(null)} />
      <CheckDialog r={checking} onClose={() => setChecking(null)} />
    </div>
  );
}
