"use client";

import type { AssistedItem } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { Hand } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { PRIVACY_KEY } from "./shared";

/** The assisted route's back office (ZID-DTV-WF-04 B02; QA-009, QA-010; CR-110). People who chose not to use the camera, face matching or a chip
 * scan wait here for a person. Nobody is asked why, and no reason is stored: the queue shows only what the case needs. A member of staff takes a
 * case, does the verification another way, and records the reference of what did the work, or why it could not be completed. Nobody handles their
 * own case. */

const ASSISTED_KEY = [...PRIVACY_KEY, "assisted"];
const REASONS = [
  { id: "PERSON_UNREACHABLE", label: "Could not reach the person" },
  { id: "PERSON_DECLINED", label: "The person declined" },
  { id: "DOCUMENT_NOT_AVAILABLE", label: "The document was not available" },
  { id: "NOT_ELIGIBLE_FOR_ROUTE", label: "This route does not apply to the case" },
  { id: "OTHER", label: "Other" },
];
const ROUTE_LABEL: Record<string, string> = {
  ASSISTED_DOCUMENT_REVIEW: "A person reviews the document",
  IN_PERSON_REVIEW: "In person",
  MANUAL_REVIEW: "Manual review",
};

export const ASSISTED_ERROR_HELP: Record<string, string> = {
  ASSISTED_OWN_SESSION: "This is your own verification. Someone else handles it.",
  ASSISTED_NOT_YOURS: "Another member of staff took this case. They complete it.",
  ASSISTED_NOT_WAITING: "Someone already took this case. Reload.",
  ASSISTED_OUTCOME_REF_REQUIRED: "Give the reference of the verification or review that did the work.",
};

function Err({ error }: { error: unknown }) {
  const code = (error as { code?: string } | null)?.code;
  if (code && ASSISTED_ERROR_HELP[code]) {
    return (
      <Banner tone="danger" title="Not done" live="assertive">
        {ASSISTED_ERROR_HELP[code]}
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

function CompleteDialog({ item, onClose }: { item: AssistedItem | null; onClose: () => void }) {
  const [outcome, setOutcome] = useState<"DONE" | "NOT_COMPLETED">("DONE");
  const [ref, setRef] = useState("");
  const [reason, setReason] = useState("PERSON_UNREACHABLE");
  const complete = useCellMutation(
    (api, x: AssistedItem) =>
      api.privacy.v2.completeAssisted(x.session_ref, outcome === "DONE" ? { done: true, outcome_ref: ref.trim() } : { done: false, reason_code: reason }),
    {
      invalidate: [ASSISTED_KEY],
      success: "Recorded",
      onSuccess: () => {
        setRef("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={item !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Record the outcome"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={complete.isPending} isDisabled={outcome === "DONE" && ref.trim().length < 2} onPress={() => item && complete.mutate(item)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select
          label="Outcome"
          value={outcome}
          onChange={(v) => setOutcome(v === "NOT_COMPLETED" ? "NOT_COMPLETED" : "DONE")}
          options={[
            { id: "DONE", label: "Verified another way" },
            { id: "NOT_COMPLETED", label: "Could not be completed" },
          ]}
        />
        {outcome === "DONE" ? (
          <TextField label="Verification or review reference" value={ref} onChange={setRef} mono isRequired />
        ) : (
          <Select label="Why" value={reason} onChange={setReason} options={REASONS} />
        )}
        <p className="text-xs text-ink-muted">Never record a medical or personal reason. Not completing never counts against the person.</p>
        {complete.error ? <Err error={complete.error} /> : null}
      </div>
    </Dialog>
  );
}

export function AssistedSection() {
  const { session } = useRuntime();
  const me = session?.authenticated ? session.subject.sub : undefined;
  const q = useCellQuery(ASSISTED_KEY, (api) => api.privacy.v2.assistedQueue());
  const [completing, setCompleting] = useState<AssistedItem | null>(null);
  const claim = useCellMutation((api, x: AssistedItem) => api.privacy.v2.claimAssisted(x.session_ref), {
    invalidate: [ASSISTED_KEY],
    success: "Taken: you handle this case",
  });
  const cols: Column<AssistedItem>[] = [
    { key: "since", header: "Waiting since", pinned: true, render: (x) => <DateTime value={x.created_at} /> },
    { key: "route", header: "Way chosen", render: (x) => ROUTE_LABEL[x.alternative_mode ?? ""] ?? x.alternative_mode ?? "—" },
    { key: "where", header: "Country · purpose", render: (x) => <span className="font-mono text-xs">{`${x.country ?? "—"} · ${x.purpose_code}`}</span> },
    {
      key: "state",
      header: "State",
      render: (x) => <Badge tone={x.assisted_state === "WAITING" ? "warn" : "info"}>{x.assisted_state === "WAITING" ? "Waiting" : "In progress"}</Badge>,
    },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (x) => {
        if (x.subject_principal_id && x.subject_principal_id === me) return <Badge tone="neutral">Your own</Badge>;
        if (x.assisted_state === "WAITING") {
          return (
            <Button size="xs" pending={claim.isPending} onPress={() => claim.mutate(x)} aria-label={`Take case ${x.session_ref}`}>
              Take
            </Button>
          );
        }
        return x.assisted_by === me ? (
          <Button size="xs" variant="secondary" onPress={() => setCompleting(x)} aria-label={`Record outcome for ${x.session_ref}`}>
            Record outcome
          </Button>
        ) : (
          <Badge tone="neutral">Taken</Badge>
        );
      },
    },
  ];
  return (
    <div className="space-y-5">
      <Card
        padded={false}
        title="Assisted verification"
        description="People who chose another way than the camera, face matching or a chip scan. Nobody is asked why; choosing it never counts against them."
      >
        <QueryView query={q}>
          {(d) => <DataTable caption="Assisted verification" columns={cols} rows={d.items} rowKey={(x) => x.session_ref} empty={<EmptyState icon={Hand} title="Nobody is waiting" />} />}
        </QueryView>
        {claim.error ? <Err error={claim.error} /> : null}
      </Card>
      <CompleteDialog item={completing} onClose={() => setCompleting(null)} />
    </div>
  );
}
