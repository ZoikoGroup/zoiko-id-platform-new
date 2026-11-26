"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiApplicationDetail, VerifyApiApproval, VerifyApiIssued } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Dialog, EmptyState, Select, TextField } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { APPROVAL_STATE, askBody, askProblem, canCancel, canDecide, canUse, describe, EMPTY_ASK, hoursLeft, type AskDraft } from "./approvals-logic";
import { parsePublicKey } from "./credential-options-logic";

const KEY = ["admin", "developers", "verify-api", "approvals"];
const APP_KEY = ["admin", "developers", "verify-api"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "");

/** Production credentials need a second administrator (CR-162). One asks, a different one approves, and the one who asked uses the approval once, within 24 hours, for exactly what it names. The server decides who may
 * approve; this screen only shows the state. */
export function CredentialApprovals({ app, onIssued }: { app: VerifyApiApplicationDetail; onIssued: (r: VerifyApiIssued) => void }) {
  const list = useCellQuery(KEY, (api) => api.verifyApi.credentialApprovals());
  const [asking, setAsking] = useState(false);
  const [using, setUsing] = useState<VerifyApiApproval | null>(null);
  const mine = (list.data?.items ?? []).filter((a) => a.application_id === app.application_id);
  return (
    <section aria-label="Production credential approvals" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Approvals for production credentials</h3>
        <Button size="sm" icon={<ShieldCheck aria-hidden className="size-4" />} isDisabled={app.status !== "ACTIVE"} onPress={() => setAsking(true)}>
          Ask for approval
        </Button>
      </div>
      <Banner tone="info" title="A second administrator is needed">
        A production credential is created or rotated only with another administrator's approval. You ask, they approve, then you use the approval once within 24 hours. It cannot be used for anything else.
      </Banner>
      {list.isPending ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : list.error ? (
        <ApiErrorView error={list.error} title="The approvals could not be loaded" onRetry={() => void list.refetch()} />
      ) : mine.length === 0 ? (
        <EmptyState title="No approvals yet" icon={ShieldCheck} compact>
          Ask for one, then have another administrator open this page and approve it.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          {mine.map((a) => (
            <ApprovalRow key={a.approval_id} a={a} onUse={() => setUsing(a)} />
          ))}
        </div>
      )}
      {asking ? <AskDialog app={app} onClose={() => setAsking(false)} /> : null}
      {using ? (
        <UseDialog
          a={using}
          onClose={() => setUsing(null)}
          onIssued={(r) => {
            setUsing(null);
            onIssued(r);
          }}
        />
      ) : null}
    </section>
  );
}

function ApprovalRow({ a, onUse }: { a: VerifyApiApproval; onUse: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const opts = { onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }) };
  const approve = useCellMutation((api) => api.verifyApi.approveApproval(a.approval_id, note), { ...opts, success: "Approved" });
  const reject = useCellMutation((api) => api.verifyApi.rejectApproval(a.approval_id, note), { ...opts, success: "Rejected" });
  const cancel = useCellMutation((api) => api.verifyApi.cancelApproval(a.approval_id), { ...opts, success: "Taken back" });
  const s = APPROVAL_STATE[a.status]!;
  const busy = approve.isPending || reject.isPending || cancel.isPending;
  const error = approve.error ?? reject.error ?? cancel.error;
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{describe(a)}</span>
        <Badge tone={s.tone}>{s.label}</Badge>
        <span className="text-xs text-ink-muted">Asked {when(a.created_at)}</span>
        {a.status === "APPROVED" ? <span className="text-xs text-ink-muted">{hoursLeft(a, new Date())} hours left to use it</span> : null}
      </div>
      <p className="text-xs text-ink-muted">{s.text}</p>
      {a.note ? <p className="text-sm">{a.note}</p> : null}
      {canDecide(a) ? (
        <div className="flex flex-wrap items-end gap-2">
          <TextField label="Note (needed to reject)" value={note} onChange={setNote} />
          <Button size="sm" isDisabled={busy} pending={approve.isPending} onPress={() => approve.mutate(undefined)}>
            Approve
          </Button>
          <Button size="sm" variant="secondary" isDisabled={busy || note.trim().length < 3} onPress={() => reject.mutate(undefined)}>
            Reject
          </Button>
          {canCancel(a) ? (
            <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => cancel.mutate(undefined)}>
              Take back
            </Button>
          ) : null}
        </div>
      ) : null}
      {canUse(a) ? (
        <Button size="sm" onPress={onUse}>
          Use this approval
        </Button>
      ) : null}
      {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (approve.reset(), reject.reset(), cancel.reset())} /> : null}
    </div>
  );
}

function AskDialog({ app, onClose }: { app: VerifyApiApplicationDetail; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<AskDraft>(EMPTY_ASK);
  const problem = askProblem(d);
  const live = app.credentials.filter((c) => c.status === "ACTIVE");
  const ask = useCellMutation((api) => api.verifyApi.requestApproval(app.application_id, askBody(d)), {
    success: "Asked. Another administrator has to approve it.",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !ask.isPending && onClose()}
      title="Ask for approval"
      description="Another administrator approves it. Then you use it once within 24 hours."
      footer={
        <>
          <Button variant="secondary" isDisabled={ask.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={ask.isPending} isDisabled={problem !== null} onPress={() => ask.mutate(undefined)}>
            Ask
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Select label="What for" options={[{ id: "ISSUE", label: "A new credential" }, { id: "ROTATE", label: "Rotating a credential" }]} value={d.action} onChange={(v) => setD({ ...d, action: v as AskDraft["action"] })} />
        {d.action === "ROTATE" ? <Select label="Credential to rotate" options={live.map((c) => ({ id: c.credential_id, label: c.client_id }))} value={d.credentialId} onChange={(v) => setD({ ...d, credentialId: v })} /> : null}
        <Select label="Signs in with" options={[{ id: "SECRET", label: "A secret" }, { id: "KEY", label: "A private key (public key registered)" }]} value={d.kind} onChange={(v) => setD({ ...d, kind: v as AskDraft["kind"] })} />
        {d.kind === "KEY" ? (
          <TextField label="Public key" value={d.keyText} onChange={(v) => setD({ ...d, keyText: v })} multiline rows={5} description="The approval is for this key only. Never paste a private key." errorMessage={parsePublicKey(d.keyText).problem ?? undefined} />
        ) : null}
        {ask.error ? <ApiErrorView error={ask.error} title="The request was not sent" onRetry={() => ask.reset()} /> : null}
      </div>
    </Dialog>
  );
}

/** Spend the approval: create or rotate the credential. A key approval needs the same public key pasted again, because only its fingerprint is kept. */
function UseDialog({ a, onClose, onIssued }: { a: VerifyApiApproval; onClose: () => void; onIssued: (r: VerifyApiIssued) => void }) {
  const qc = useQueryClient();
  const [keyText, setKeyText] = useState("");
  const key = parsePublicKey(keyText);
  const needKey = a.with_key;
  const ready = !needKey || key.jwk !== null;
  const go = useCellMutation(
    (api) =>
      a.action === "ROTATE"
        ? api.verifyApi.rotateCredential(a.application_id, a.credential_id!, 24, key.jwk ?? undefined, a.approval_id)
        : api.verifyApi.issueCredential(a.application_id, key.jwk ?? undefined, a.approval_id),
    {
      onSuccess: (r) => {
        void qc.invalidateQueries({ queryKey: KEY });
        void qc.invalidateQueries({ queryKey: APP_KEY });
        onIssued(r);
      },
    },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !go.isPending && onClose()}
      title={describe(a)}
      description="This uses the approval. It cannot be used again."
      footer={
        <>
          <Button variant="secondary" isDisabled={go.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={go.isPending} isDisabled={!ready} onPress={() => go.mutate(undefined)}>
            Create now
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {needKey ? <TextField label="The same public key" value={keyText} onChange={setKeyText} multiline rows={5} description="It must be exactly the key that was approved." errorMessage={key.problem ?? undefined} /> : null}
        {a.action === "ROTATE" ? <p className="text-xs text-ink-muted">The old credential keeps working for 24 hours.</p> : null}
        {go.error ? <ApiErrorView error={go.error} title="The credential was not made" onRetry={() => go.reset()} /> : null}
      </div>
    </Dialog>
  );
}
