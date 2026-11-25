"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiCase } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { Scale } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { canWithdraw, CASE_STATE, draftProblem, EMPTY_DRAFT, KINDS, kindLabel, outcomeText, toRequest, type CaseDraft, type CaseKind } from "./cases-logic";

const KEY = ["admin", "developers", "verify-api", "cases"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "");

/** Disputes, appeals and erasure requests (CR-159). A dispute or an appeal never changes a result: a person at Zoiko iD reviews it and the note comes back here. An erasure removes a customer's reference from the
 * sessions it was sent with, and the results stay. */
export function CasesCard() {
  const list = useCellQuery(KEY, (api) => api.verifyApi.cases());
  const [raising, setRaising] = useState(false);
  const [open, setOpen] = useState<VerifyApiCase | null>(null);
  const columns: Column<VerifyApiCase>[] = [
    { key: "k", header: "Request", pinned: true, render: (c) => <span className="font-medium">{kindLabel(c.kind)}</span>, sortValue: (c) => c.kind },
    { key: "s", header: "State", render: (c) => <Badge tone={CASE_STATE[c.status]?.tone ?? "neutral"}>{CASE_STATE[c.status]?.label ?? c.status}</Badge> },
    { key: "t", header: "About", render: (c) => (c.session_id ? <span className="font-mono text-xs">{c.session_id}</span> : <span className="text-xs text-ink-muted">A customer reference</span>) },
    { key: "c", header: "Sent", render: (c) => when(c.created_at), sortValue: (c) => c.created_at },
  ];
  return (
    <>
      <QueryView query={list}>
        {(d) => (
          <Card
            title="Disputes, appeals and erasure"
            description="Ask a person at Zoiko iD to look at a result, or to remove a customer's reference. Results are never changed behind your back."
            actions={
              <Button size="sm" icon={<Scale aria-hidden className="size-4" />} onPress={() => setRaising(true)}>
                New request
              </Button>
            }
            padded={false}
          >
            <DataTable<VerifyApiCase>
              caption="Disputes, appeals and erasure requests"
              columns={columns}
              rows={d.items}
              rowKey={(c) => c.case_id}
              onRowAction={(c) => setOpen(c)}
              empty={
                <EmptyState title="No requests" icon={Scale} compact>
                  When you disagree with a result, or a customer asks to be forgotten, send a request here.
                </EmptyState>
              }
            />
          </Card>
        )}
      </QueryView>
      {raising ? <RaiseDialog onClose={() => setRaising(false)} /> : null}
      {open ? <CaseDialog c={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function RaiseDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<CaseDraft>(EMPTY_DRAFT);
  const kind = KINDS.find((k) => k.id === draft.kind)!;
  const problem = draftProblem(draft);
  const send = useCellMutation((api) => api.verifyApi.raiseCase(toRequest(draft)), {
    success: "Request sent",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !send.isPending && onClose()}
      title="New request"
      description="A person at Zoiko iD reviews it. You can withdraw it until it is decided."
      footer={
        <>
          <Button variant="secondary" isDisabled={send.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={send.isPending} isDisabled={problem !== null} onPress={() => send.mutate(undefined)}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select label="What do you need?" options={KINDS.map((k) => ({ id: k.id, label: k.label }))} value={draft.kind} onChange={(v) => setDraft({ ...draft, kind: v as CaseKind, target: "" })} />
        <p className="text-xs text-ink-muted">{kind.help}</p>
        <TextField
          label={kind.target === "session" ? "Session ID" : "Customer reference"}
          value={draft.target}
          onChange={(v) => setDraft({ ...draft, target: v })}
          description={kind.target === "session" ? "Starts with vfs_. You can copy it from the results list." : "Exactly as you sent it when you started the verification."}
        />
        <TextField label="Why" value={draft.reason} onChange={(v) => setDraft({ ...draft, reason: v })} multiline rows={4} description="The reviewer reads this. Do not include anyone's personal details." />
        {draft.target || draft.reason ? <p className="text-xs text-ink-muted">{problem}</p> : null}
        {send.error ? <ApiErrorView error={send.error} title="The request was not sent" onRetry={() => send.reset()} /> : null}
      </div>
    </Dialog>
  );
}

function CaseDialog({ c, onClose }: { c: VerifyApiCase; onClose: () => void }) {
  const qc = useQueryClient();
  const s = CASE_STATE[c.status];
  const outcome = outcomeText(c);
  const withdraw = useCellMutation((api) => api.verifyApi.withdrawCase(c.case_id), {
    success: "Request withdrawn",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !withdraw.isPending && onClose()}
      title={kindLabel(c.kind)}
      description={s?.text}
      footer={
        <>
          {canWithdraw(c) ? (
            <Button variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate(undefined)}>
              Withdraw request
            </Button>
          ) : null}
          <Button onPress={onClose}>Close</Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={s?.tone ?? "neutral"}>{s?.label ?? c.status}</Badge>
          <span className="text-ink-muted">Sent {when(c.created_at)}</span>
          {c.decided_at ? <span className="text-ink-muted">Decided {when(c.decided_at)}</span> : null}
        </div>
        {c.session_id ? <p className="font-mono text-xs">{c.session_id}</p> : <p className="text-xs text-ink-muted">About a customer reference (it is not shown here).</p>}
        <div>
          <p className="text-xs font-medium text-ink-muted">What you said</p>
          <p>{c.reason}</p>
        </div>
        {c.note ? (
          <Banner tone={c.status === "REJECTED" ? "danger" : "ok"} title="From Zoiko iD">
            {c.note}
          </Banner>
        ) : null}
        {outcome ? <p className="text-ink">{outcome}</p> : null}
        {withdraw.error ? <ApiErrorView error={withdraw.error} title="The request was not withdrawn" onRetry={() => withdraw.reset()} /> : null}
      </div>
    </Dialog>
  );
}
