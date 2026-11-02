"use client";

import { ApiError, type OrgApprovalCase } from "@zoiko/api-client";
import { Banner, Button, DateTime, Dialog, TextField } from "@zoiko/ui";
import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useTenant } from "@/features/onboarding/shared";
import { approvalRefusalText } from "@/lib/approvals";
import { ApiErrorView, useCellMutation } from "@/lib/cell";
import { StepUpCancelled } from "@/lib/step-up";

import { CONFLICT_MESSAGE, refusalKind, resolveOrgId } from "./logic";

/** The organization this page is about: the path's id, or the caller's own for `current`. The server answers only for the caller's own. */
export function useOrgId(param: string): { orgId: string | null; loading: boolean; error: unknown } {
  const tenant = useTenant();
  const orgId = resolveOrgId(param, tenant.data?.tenant_id);
  return { orgId, loading: param === "current" && tenant.isLoading, error: tenant.error };
}

/** A refusal in the records' words. A cancelled passkey prompt is not an error, and a stale version says to refresh. */
export function Refusal({ error, title }: { error: unknown; title: string }) {
  if (error instanceof StepUpCancelled) return null;
  if (error instanceof ApiError) {
    const kind = refusalKind(error.code, error.status);
    if (kind === "conflict")
      return (
        <Banner tone="warn" title="This request changed" live="assertive">
          {CONFLICT_MESSAGE}
        </Banner>
      );
    if (kind === "refused")
      return (
        <Banner tone="warn" title={title} live="polite">
          {approvalRefusalText(error.code)}
        </Banner>
      );
  }
  return <ApiErrorView error={error} title={title} />;
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className="break-words text-ink">{children}</dd>
    </div>
  );
}

/** A secret the change produced (an enrollment link). Held in memory for this view only, shown once, never stored. */
export function ShownOnce({ secret, onDone }: { secret: Record<string, unknown>; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const ticket = typeof secret.enrollment_ticket === "string" ? secret.enrollment_ticket : null;
  return (
    <Banner tone="warn" title="Copy this now. It is shown once." live="polite">
      <div className="space-y-2">
        <p>This single-use link is not stored and cannot be shown again. Give it only to the person it is for.</p>
        {ticket ? (
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-sunken px-2 py-1 text-xs">{ticket}</code>
            <Button
              size="sm"
              variant="secondary"
              className="min-h-11"
              icon={copied ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
              onPress={() => void navigator.clipboard.writeText(ticket).then(() => setCopied(true))}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        ) : null}
        {typeof secret.expires_at === "string" ? (
          <p className="text-xs">
            Expires <DateTime value={secret.expires_at} />.
          </p>
        ) : null}
        <Button size="sm" className="min-h-11" onPress={onDone}>
          I have copied it
        </Button>
      </div>
    </Banner>
  );
}

/** A material change is a new version: every vote on the old one is void and the person who changes it can never vote. */
export function ReviseDialog({
  c,
  isOpen,
  onOpenChange,
  onDone,
}: {
  c: OrgApprovalCase;
  isOpen: boolean;
  onOpenChange: (o: boolean) => void;
  onDone: (id: string) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(c.payload, null, 2));
  const [reason, setReason] = useState("");
  const parsed = (() => {
    try {
      const v: unknown = JSON.parse(text);
      return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();
  const revise = useCellMutation((api) => api.approvals.revise(c.case_id, parsed ?? {}, reason.trim()), {
    invalidate: [["org-approvals"]],
    success: "A new version was created",
    onSuccess: (n) => onDone(n.case_id),
  });
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      title="Revise and resubmit"
      description="This creates a new version. Approvals already given to this version are void, and you will not be able to approve the new one."
      footer={
        <>
          <Button variant="secondary" className="min-h-11" onPress={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button className="min-h-11" isDisabled={!parsed || !reason.trim()} pending={revise.isPending} onPress={() => revise.mutate(undefined)}>
            Create new version
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Proposed change (JSON)" value={text} onChange={setText} multiline rows={6} mono errorMessage={parsed ? undefined : "This is not a valid JSON object."} />
        <TextField label="Why it changed" value={reason} onChange={setReason} isRequired />
        {revise.error ? <Refusal error={revise.error} title="The new version was not created" /> : null}
      </div>
    </Dialog>
  );
}
