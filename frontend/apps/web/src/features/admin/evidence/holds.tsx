"use client";

import type { LegalHold } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, DateTime, Dialog, EmptyState, RiskBadge, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Lock, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, listInput, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const HOLDS_KEY = ["admin", "evidence", "holds"];

export function HoldsSection() {
  const holds = useCellQuery(HOLDS_KEY, (api) => api.evidence.holds());
  const [creating, setCreating] = useState(false);
  const [releasing, setReleasing] = useState<LegalHold | null>(null);
  const [reason, setReason] = useState("");
  const release = useCellMutation((api, h: LegalHold) => api.evidence.releaseHold(h.legal_hold_id, { reason: reason.trim() }), {
    invalidate: [HOLDS_KEY],
    success: "Legal hold released; ordinary retention resumes from the original schedule",
    onSuccess: () => {
      setReleasing(null);
      setReason("");
    },
  });

  const columns: Column<LegalHold>[] = [
    {
      key: "reference",
      header: "Matter",
      pinned: true,
      render: (h) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{h.reference}</span>
          <span className="text-2xs text-ink-subtle">{h.basis}</span>
        </span>
      ),
    },
    { key: "status", header: "Status", render: (h) => <StatusBadge state={h.status} kind="Legal hold" /> },
    {
      key: "scope",
      header: "Scope",
      render: (h) => (
        <span className="text-xs">
          {h.subject_count ? `${h.subject_count} subject(s)` : "All subjects"} · {h.object_types.length ? h.object_types.join(", ") : "all object types"}
        </span>
      ),
    },
    {
      key: "review",
      header: "Review",
      render: (h) => (
        <span className="inline-flex items-center gap-1.5">
          <DateTime value={h.review_at} style="date" />
          {h.review_overdue ? <Badge tone="warn">overdue</Badge> : null}
        </span>
      ),
    },
    { key: "expires", header: "Expires", render: (h) => (h.expires_at ? <DateTime value={h.expires_at} style="date" /> : "On release") },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (h) =>
        h.status === "ACTIVE" ? (
          <Button size="xs" variant="secondary" onPress={() => setReleasing(h)}>
            Release
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Legal holds"
        description="A hold suspends deletion and crypto-erasure only for the subjects and object types it names; it is never tenant-wide by default."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            New hold
          </Button>
        }
      >
        <QueryView query={holds}>
          {(data) => (
            <DataTable
              caption="Legal holds"
              columns={columns}
              rows={data.items}
              rowKey={(h) => h.legal_hold_id}
              empty={<EmptyState icon={Lock} title="No legal holds">Retention and erasure run on their ordinary schedules.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      <CreateHoldDialog isOpen={creating} onClose={() => setCreating(false)} />
      <Dialog
        isOpen={releasing !== null}
        onOpenChange={(o) => !o && !release.isPending && setReleasing(null)}
        title="Release this legal hold?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" onPress={() => setReleasing(null)}>
              Cancel
            </Button>
            <Button pending={release.isPending} isDisabled={reason.trim().length < 3} onPress={() => releasing && release.mutate(releasing)}>
              Release hold
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-ink-muted">
            Held items become subject to their original retention dates again; anything already past due is disposed on the next sweep. The
            requester of a hold cannot release it.
          </p>
          <TextField label="Reason" value={reason} onChange={setReason} isRequired />
          {release.error ? <ApiErrorView error={release.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

function CreateHoldDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [basis, setBasis] = useState("");
  const [reference, setReference] = useState("");
  const [subjects, setSubjects] = useState("");
  const [types, setTypes] = useState("");
  const [review, setReview] = useState("");
  const [approval, setApproval] = useState("");
  const create = useCellMutation(
    (api) =>
      api.evidence.createHold({
        basis: basis.trim(),
        reference: reference.trim(),
        subject_principal_ids: listInput(subjects),
        object_types: listInput(types),
        review_at: new Date(review).toISOString(),
        approval_request_id: approval.trim(),
      }),
    { invalidate: [HOLDS_KEY], success: "Legal hold applied", onSuccess: () => close() },
  );
  function close() {
    if (create.isPending) return;
    setBasis("");
    setReference("");
    setSubjects("");
    setTypes("");
    setReview("");
    setApproval("");
    create.reset();
    onClose();
  }
  const valid = basis.trim().length >= 3 && reference.trim() && (subjects.trim() || types.trim()) && review && approval.trim();
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Apply a legal hold"
      description="Two-person rule: a second administrator approves this exact scope first (approval request bound to the matter reference, subjects and object types)."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={close}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Apply hold
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <TextField label="Legal basis" value={basis} onChange={setBasis} isRequired placeholder="Litigation hold" />
        <TextField label="Matter / case reference" value={reference} onChange={setReference} isRequired mono />
        <TextField label="Subject principal IDs" value={subjects} onChange={setSubjects} mono multiline rows={3} description="Comma or newline separated." />
        <TextField label="Object types" value={types} onChange={setTypes} mono placeholder="verification.document, authority.registry_extract" />
        <TextField label="Review date" type="date" value={review} onChange={setReview} isRequired />
        <TextField label="Approval request ID" value={approval} onChange={setApproval} isRequired mono />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}
