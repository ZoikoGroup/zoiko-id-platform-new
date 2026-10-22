"use client";

import type { OverrideIn, SubjectType } from "@zoiko/api-client";
import { Banner, Button, Card, DateTime, Dialog, KeyValues, RadioCards, RevisionChip, RiskBadge, Select, StatusBadge, TextField } from "@zoiko/ui";
import { Info, Search, ShieldQuestion } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { isUuid, OVERRIDE_STATES, SUBJECT_TYPES, type OverrideState } from "./shared";

type Subject = { type: SubjectType; id: string };

const stateKey = (s: Subject) => ["admin", "signals", "security-state", s.type, s.id];

function OverrideDialog({ subject, onClose }: { subject: Subject | null; onClose: () => void }) {
  const [state, setState] = useState<OverrideState>("TRUSTED");
  const [reason, setReason] = useState("");
  const [approval, setApproval] = useState("");
  const override = useCellMutation((api, v: { s: Subject; body: OverrideIn }) => api.signals.override(v.s.type, v.s.id, v.body), {
    invalidate: subject ? [stateKey(subject)] : [],
    success: (r) => `Security state set to ${r.state} (revision ${r.revision})`,
    onSuccess: () => close(),
  });

  function close() {
    setState("TRUSTED");
    setReason("");
    setApproval("");
    override.reset();
    onClose();
  }

  const reasonOk = reason.trim().length >= 3;
  return (
    <Dialog
      isOpen={subject !== null}
      onOpenChange={(o) => !o && !override.isPending && close()}
      title="Re-evaluate security state"
      description="A governed override, recorded like any other signal."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={override.isPending} onPress={close}>
            Cancel
          </Button>
          <Button
            variant="danger"
            pending={override.isPending}
            isDisabled={!reasonOk}
            onPress={() =>
              subject &&
              override.mutate({ s: subject, body: { state, reason: reason.trim(), approval_request_id: approval.trim() || null } })
            }
          >
            Apply override
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
          <RiskBadge risk="R3" />
          <span>
            Subject <code className="font-mono text-xs">{subject?.type}</code> <code className="font-mono text-xs">{subject?.id}</code>
          </span>
        </div>
        <RadioCards label="New state" value={state} onChange={(v) => setState(v as OverrideState)} options={OVERRIDE_STATES.map((s) => ({ ...s }))} />
        <TextField
          label="Justification"
          isRequired
          multiline
          rows={3}
          value={reason}
          onChange={setReason}
          description="3–500 characters. Recorded with the override in the evidence trail."
        />
        <TextField
          label="Approval request ID (optional)"
          value={approval}
          onChange={setApproval}
          mono
          description="Needed when tenant policy requires a second administrator for this change. The approval must name this exact state."
        />
        <Banner tone="info" icon={Info}>
          Terminal states (Compromised, Revoked, Purged) cannot be cleared by an override.
        </Banner>
        {override.error ? <ApiErrorView error={override.error} /> : null}
      </div>
    </Dialog>
  );
}

export function SecurityStateSection() {
  const [type, setType] = useState<SubjectType>("PRINCIPAL");
  const [id, setId] = useState("");
  const [invalid, setInvalid] = useState<string | undefined>();
  const [subject, setSubject] = useState<Subject | null>(null);
  const [overriding, setOverriding] = useState<Subject | null>(null);

  const q = useCellQuery(subject ? stateKey(subject) : ["admin", "signals", "security-state", "none"], (api) => api.signals.securityState(subject!.type, subject!.id), {
    enabled: Boolean(subject),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!isUuid(id)) return setInvalid("Enter the subject's UUID.");
    setInvalid(undefined);
    setSubject({ type, id: id.trim().toLowerCase() });
  };

  return (
    <div className="space-y-5">
      <Card title="Look up security state" description="Current state of a principal, session or credential as decided by the signal rules.">
        <form onSubmit={submit} role="search" className="flex flex-wrap items-start gap-3">
          <Select
            label="Subject type"
            className="w-48"
            options={SUBJECT_TYPES}
            value={type}
            onChange={(v) => setType(v as SubjectType)}
          />
          <TextField label="Subject ID" value={id} onChange={setId} mono className="min-w-64 flex-1" errorMessage={invalid} placeholder="00000000-0000-0000-0000-000000000000" />
          <Button type="submit" variant="secondary" className="mt-6.5" icon={<Search aria-hidden className="size-4" />}>
            Look up
          </Button>
        </form>
      </Card>

      {subject ? (
        <Card
          title="Security state"
          actions={
            <Button size="sm" variant="danger-outline" onPress={() => setOverriding(subject)}>
              Override / re-evaluate
            </Button>
          }
        >
          <QueryView query={q} loadingRows={2}>
            {(s) => (
              <div className="space-y-4">
                <KeyValues
                  columns={3}
                  items={[
                    ["State", <StatusBadge key="s" state={s.state} kind="Security state" />],
                    ["Revision", <RevisionChip key="r" kind="Security" value={s.revision} />],
                    ["Subject", <code key="id" className="font-mono text-xs break-all">{s.subject_type} {s.subject_id}</code>],
                    ["Effective at", <DateTime key="e" value={s.effective_at} utc />],
                    ["Committed at", <DateTime key="c" value={s.committed_at} utc />],
                  ]}
                />
                {s.revision === 0 ? (
                  <p className="flex items-center gap-2 text-xs text-ink-subtle">
                    <ShieldQuestion aria-hidden className="size-4" />
                    No signal has been recorded for this subject; it is trusted by default.
                  </p>
                ) : null}
                <p className="text-xs text-ink-subtle">
                  The cell does not return the reason codes behind a state here; see the subject&apos;s signal history in the audit trail.
                </p>
              </div>
            )}
          </QueryView>
        </Card>
      ) : null}

      <OverrideDialog subject={overriding} onClose={() => setOverriding(null)} />
    </div>
  );
}
