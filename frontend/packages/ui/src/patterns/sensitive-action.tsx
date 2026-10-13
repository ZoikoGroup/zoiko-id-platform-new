"use client";

/** Sensitive Action Interaction Pattern (ZID-UX-001 §22, §26; UX-05, UX-25, UX-32).
 *
 * Intent → Impact → Preconditions → Approval/step-up → Commit → Result → Observe.
 * The commit carries an idempotency key (stable for this dialog instance) and the expected object
 * version, so a retry never double-applies and a concurrent change surfaces as a conflict. The UI
 * only presents; the backend decides and returns the canonical outcome + receipt. */
import {
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Fingerprint,
  Loader2,
  RotateCcw,
  ShieldAlert,
  Undo2,
  XCircle,
} from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";

import { EvidenceChip, RevisionChip, RiskBadge, StatusBadge } from "../components/badges";
import { Button } from "../components/button";
import { CopyId } from "../components/data";
import { Banner, ConflictNotice, ErrorState } from "../components/feedback";
import { Checkbox, ReasonCodeField, TextField, type Option } from "../components/forms";
import { Dialog } from "../components/overlay";
import { cn } from "../utils/cn";

export type Reversibility = "reversible" | "reversible-with-effort" | "irreversible";

export type Precondition = {
  label: string;
  state: "pass" | "fail" | "pending";
  detail?: ReactNode;
};

export type CommitContext = {
  idempotencyKey: string;
  expectedVersion?: string | number;
  reasonCode: string | null;
  note: string;
};

export type CommitResult =
  | {
      kind: "done";
      outcome: string;
      reasonCode?: string;
      objectId: string;
      revision?: string | number;
      receiptId?: string;
      /** §22 step 7: propagation / dependent state. */
      observe?: { label: string; state: string }[];
      rollback?: ReactNode;
    }
  | { kind: "approval-requested"; approvalRequestId: string; approvers: string; expiresAt?: string }
  | { kind: "conflict"; expected: string | number; current: string | number }
  | { kind: "error"; error: unknown };

export type SensitiveActionProps = {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  /** Verb phrase: "Revoke mandate M-441". */
  title: string;
  /** Step 1: what will happen and to what scope. */
  intent: ReactNode;
  scope?: [string, ReactNode][];
  risk: "R0" | "R1" | "R2" | "R3";
  evidenceClass: "E0" | "E1" | "E2" | "E3";
  reversibility: Reversibility;
  /** Step 2: affected principals/resources and dependents (UX-25). */
  affected?: { label: string; detail?: ReactNode }[];
  dependents?: { label: string; effect: string }[];
  evidenceConsequence?: ReactNode;
  /** Step 3. Any failed precondition blocks commit. */
  preconditions?: Precondition[];
  reasons?: Option[];
  /** Step 4. */
  stepUp?: { required: boolean; perform: () => Promise<boolean>; level?: string };
  approval?: { required: boolean; summary: string };
  /** Typed confirmation, only for exceptional irreversible actions (§4 "Destructive modal"). */
  typedConfirmation?: string;
  expectedVersion?: string | number;
  commitLabel: string;
  onCommit: (ctx: CommitContext) => Promise<CommitResult>;
  /** Called when the user chooses to refresh after a conflict or closes after a result. */
  onSettled?: () => void;
};

const STEPS = ["Intent", "Impact", "Preconditions", "Approval", "Commit", "Result", "Observe"] as const;

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

const REVERSIBILITY: Record<Reversibility, { text: string; tone: "ok" | "warn" | "danger" }> = {
  reversible: { text: "Reversible", tone: "ok" },
  "reversible-with-effort": { text: "Reversible, with dependent effects", tone: "warn" },
  irreversible: { text: "Irreversible", tone: "danger" },
};

export function SensitiveActionDialog(props: SensitiveActionProps) {
  const {
    isOpen,
    onOpenChange,
    title,
    intent,
    scope,
    risk,
    evidenceClass,
    reversibility,
    affected = [],
    dependents = [],
    evidenceConsequence,
    preconditions = [],
    reasons,
    stepUp,
    approval,
    typedConfirmation,
    expectedVersion,
    commitLabel,
    onCommit,
    onSettled,
  } = props;

  const [page, setPage] = useState<"review" | "authorize" | "result">("review");
  const [reasonCode, setReasonCode] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [typed, setTyped] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [steppedUp, setSteppedUp] = useState(false);
  const [stepUpPending, setStepUpPending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [showReasonError, setShowReasonError] = useState(false);
  const idempotencyKey = useMemo(() => (isOpen ? newIdempotencyKey() : ""), [isOpen]);
  const ackId = useId();

  useEffect(() => {
    if (!isOpen) {
      setPage("review");
      setReasonCode(null);
      setNote("");
      setTyped("");
      setAcknowledged(false);
      setSteppedUp(false);
      setResult(null);
      setShowReasonError(false);
    }
  }, [isOpen]);

  const blocked = preconditions.some((p) => p.state !== "pass");
  const needsStepUp = Boolean(stepUp?.required) && !steppedUp;
  const typedOk = !typedConfirmation || typed.trim() === typedConfirmation;
  const reasonOk = !reasons?.length || Boolean(reasonCode);
  const irreversible = reversibility === "irreversible";
  const canCommit = !blocked && !needsStepUp && typedOk && reasonOk && (!irreversible || acknowledged);

  const activeStep =
    page === "review" ? 1 : page === "authorize" ? (needsStepUp || approval?.required ? 3 : 4) : result?.kind === "done" && result.observe ? 6 : 5;

  async function commit() {
    if (!reasonOk) {
      setShowReasonError(true);
      return;
    }
    setSubmitting(true);
    try {
      const r = await onCommit({ idempotencyKey, expectedVersion, reasonCode, note });
      setResult(r);
      setPage("result");
    } catch (error) {
      setResult({ kind: "error", error });
      setPage("result");
    } finally {
      setSubmitting(false);
    }
  }

  const rev = REVERSIBILITY[reversibility];

  const footer =
    page === "review" ? (
      <>
        <Button variant="secondary" onPress={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onPress={() => setPage("authorize")} iconEnd={<ArrowRight aria-hidden className="size-4 rtl:rotate-180" />}>
          Continue to checks
        </Button>
      </>
    ) : page === "authorize" ? (
      <>
        <Button variant="ghost" onPress={() => setPage("review")}>
          Back
        </Button>
        <Button
          variant={irreversible || risk === "R3" ? "danger" : "primary"}
          isDisabled={!canCommit}
          pending={submitting}
          onPress={() => void commit()}
        >
          {approval?.required ? "Request approval" : commitLabel}
        </Button>
      </>
    ) : (
      <Button
        onPress={() => {
          onOpenChange(false);
          onSettled?.();
        }}
      >
        Done
      </Button>
    );

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !submitting && onOpenChange(o)}
      isDismissable={!submitting}
      title={title}
      size="lg"
      role="alertdialog"
      footer={footer}
    >
      <ol aria-label="Progress" className="mb-5 flex flex-wrap gap-x-1 gap-y-2 text-2xs">
        {STEPS.map((s, i) => {
          const state = i < activeStep ? "done" : i === activeStep ? "current" : "todo";
          return (
            <li key={s} aria-current={state === "current" ? "step" : undefined} className="flex items-center gap-1">
              <span
                className={cn(
                  "flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold uppercase tracking-wide",
                  state === "done" && "bg-ok-bg text-ok-fg",
                  state === "current" && "bg-brand-600 text-on-brand",
                  state === "todo" && "bg-sunken text-ink-subtle",
                )}
              >
                {state === "done" ? <CheckCircle2 aria-hidden className="size-3" /> : <span aria-hidden>{i + 1}</span>}
                {s}
                <span className="sr-only">{state === "done" ? " (complete)" : state === "current" ? " (current)" : ""}</span>
              </span>
            </li>
          );
        })}
      </ol>

      {page === "review" ? (
        <div className="space-y-5">
          <section aria-labelledby="sa-intent">
            <h3 id="sa-intent" className="mb-2 text-sm font-semibold text-ink">
              What will happen
            </h3>
            <div className="text-sm text-ink-muted">{intent}</div>
            {scope?.length ? (
              <dl className="mt-3 grid gap-x-6 gap-y-2 rounded-lg border border-border bg-canvas/60 p-3 text-sm sm:grid-cols-2">
                {scope.map(([k, v]) => (
                  <div key={k} className="min-w-0">
                    <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{k}</dt>
                    <dd className="mt-0.5 text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </section>

          <section aria-labelledby="sa-impact">
            <h3 id="sa-impact" className="mb-2 text-sm font-semibold text-ink">
              Impact
            </h3>
            <div className="flex flex-wrap gap-1.5">
              <RiskBadge risk={risk} />
              <EvidenceChip evidenceClass={evidenceClass} receipt={evidenceClass !== "E0"} />
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
                  rev.tone === "ok" && "bg-ok-bg text-ok-fg ring-ok-fg/20",
                  rev.tone === "warn" && "bg-warn-bg text-warn-fg ring-warn-fg/25",
                  rev.tone === "danger" && "bg-danger-bg text-danger-fg ring-danger-fg/20",
                )}
              >
                {rev.tone === "danger" ? <ShieldAlert aria-hidden className="size-3.5" /> : <Undo2 aria-hidden className="size-3.5" />}
                {rev.text}
              </span>
            </div>
            {affected.length ? (
              <div className="mt-3">
                <div className="text-xs font-medium text-ink-muted">Directly affected ({affected.length})</div>
                <ul className="mt-1 divide-y divide-border rounded-lg border border-border">
                  {affected.map((a) => (
                    <li key={a.label} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                      <span className="font-medium text-ink">{a.label}</span>
                      {a.detail ? <span className="text-ink-muted">{a.detail}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {dependents.length ? (
              <div className="mt-3">
                <div className="text-xs font-medium text-ink-muted">Dependent objects ({dependents.length})</div>
                <ul className="mt-1 divide-y divide-border rounded-lg border border-border">
                  {dependents.map((d) => (
                    <li key={d.label} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                      <span className="text-ink">{d.label}</span>
                      <span className="text-warn-fg">{d.effect}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {evidenceConsequence ? <p className="mt-3 text-sm text-ink-muted">{evidenceConsequence}</p> : null}
          </section>
        </div>
      ) : null}

      {page === "authorize" ? (
        <div className="space-y-5">
          {preconditions.length ? (
            <section aria-labelledby="sa-pre">
              <h3 id="sa-pre" className="mb-2 text-sm font-semibold text-ink">
                Preconditions
              </h3>
              <ul className="space-y-1.5">
                {preconditions.map((p) => (
                  <li key={p.label} className="flex items-start gap-2 text-sm">
                    {p.state === "pass" ? (
                      <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" />
                    ) : p.state === "fail" ? (
                      <XCircle aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-fg" />
                    ) : (
                      <CircleDashed aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
                    )}
                    <span>
                      <span className="text-ink">{p.label}</span>
                      <span className="sr-only">: {p.state}</span>
                      {p.detail ? <span className="block text-xs text-ink-muted">{p.detail}</span> : null}
                    </span>
                  </li>
                ))}
              </ul>
              {blocked ? (
                <Banner tone="danger" className="mt-3" title="This action is blocked">
                  Resolve the failed or pending checks first. The backend would refuse the change.
                </Banner>
              ) : null}
            </section>
          ) : null}

          {reasons?.length ? (
            <ReasonCodeField
              reasons={reasons}
              code={reasonCode}
              onCodeChange={(c) => {
                setReasonCode(c);
                setShowReasonError(false);
              }}
              note={note}
              onNoteChange={setNote}
              errorMessage={showReasonError ? "Choose a reason code." : undefined}
            />
          ) : null}

          {stepUp?.required ? (
            <section aria-labelledby="sa-stepup" className="rounded-lg border border-border p-4">
              <h3 id="sa-stepup" className="flex items-center gap-2 text-sm font-semibold text-ink">
                <Fingerprint aria-hidden className="size-4" />
                Re-authenticate to continue
              </h3>
              <p className="mt-1 text-sm text-ink-muted">
                This action needs {stepUp.level ?? "a fresh, phishing-resistant"} authentication. Your entries here are kept.
              </p>
              <div className="mt-3">
                {steppedUp ? (
                  <StatusBadge state="VERIFIED" kind="Step-up" />
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    pending={stepUpPending}
                    icon={<Fingerprint aria-hidden className="size-4" />}
                    onPress={async () => {
                      setStepUpPending(true);
                      try {
                        setSteppedUp(await stepUp.perform());
                      } finally {
                        setStepUpPending(false);
                      }
                    }}
                  >
                    Verify with passkey
                  </Button>
                )}
              </div>
            </section>
          ) : null}

          {approval?.required ? (
            <Banner tone="info" title="Approval required">
              {approval.summary} Submitting creates an approval request; nothing changes until it is approved.
            </Banner>
          ) : null}

          {irreversible ? (
            <div className="rounded-lg border border-danger-fg/30 bg-danger-bg p-3">
              <Checkbox isSelected={acknowledged} onChange={setAcknowledged} aria-label={undefined}>
                <span id={ackId}>I understand this cannot be undone and that the evidence record is retained.</span>
              </Checkbox>
            </div>
          ) : null}

          {typedConfirmation ? (
            <TextField
              label={`Type "${typedConfirmation}" to confirm`}
              value={typed}
              onChange={setTyped}
              mono
              autoComplete="off"
              description="Required only for exceptional irreversible actions."
            />
          ) : null}

          <p className="text-xs text-ink-subtle">
            Submitted once with idempotency key <span className="font-mono">{idempotencyKey.slice(0, 8)}</span>
            {expectedVersion !== undefined ? (
              <>
                {" "}
                against version <span className="font-mono">{expectedVersion}</span>
              </>
            ) : null}
            . The platform re-authorizes this request.
          </p>
        </div>
      ) : null}

      {page === "result" && result ? <ResultView result={result} onRetry={() => setPage("authorize")} onRefresh={() => { onOpenChange(false); onSettled?.(); }} /> : null}

      {submitting ? (
        <div role="status" className="sr-only">
          <Loader2 aria-hidden /> Submitting
        </div>
      ) : null}
    </Dialog>
  );
}

function ResultView({ result, onRetry, onRefresh }: { result: CommitResult; onRetry: () => void; onRefresh: () => void }) {
  if (result.kind === "conflict") {
    return <ConflictNotice expected={result.expected} current={result.current} onRefresh={onRefresh} />;
  }
  if (result.kind === "error") {
    return (
      <div className="space-y-3">
        <ErrorState error={result.error} title="The change was not applied" />
        <Button variant="secondary" size="sm" icon={<RotateCcw aria-hidden className="size-4" />} onPress={onRetry}>
          Review and try again
        </Button>
        <p className="text-xs text-ink-subtle">Retrying reuses the same idempotency key, so the change can never be applied twice.</p>
      </div>
    );
  }
  if (result.kind === "approval-requested") {
    return (
      <div className="space-y-3" role="status">
        <Banner tone="info" title="Approval requested">
          Waiting for {result.approvers}. Nothing has changed yet.
        </Banner>
        <div className="text-sm">
          Approval request <CopyId value={result.approvalRequestId} label="approval request" />
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4" role="status">
      <div className="flex items-start gap-3 rounded-lg border border-border bg-canvas/60 p-4">
        <CheckCircle2 aria-hidden className="mt-0.5 size-5 text-ok-fg" />
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
            Outcome <StatusBadge state={result.outcome} kind="Outcome" />
            {result.reasonCode ? <span className="font-mono text-xs font-normal text-ink-muted">{result.reasonCode}</span> : null}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
            <span>
              Object <CopyId value={result.objectId} label="object" />
            </span>
            {result.revision !== undefined ? <RevisionChip kind="rev" value={result.revision} /> : null}
            {result.receiptId ? (
              <span>
                Receipt <CopyId value={result.receiptId} label="decision receipt" />
              </span>
            ) : null}
          </div>
        </div>
      </div>
      {result.observe?.length ? (
        <section aria-labelledby="sa-observe">
          <h3 id="sa-observe" className="mb-2 text-sm font-semibold text-ink">
            Propagation and dependent state
          </h3>
          <ul className="divide-y divide-border rounded-lg border border-border">
            {result.observe.map((o) => (
              <li key={o.label} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                <span>{o.label}</span>
                <StatusBadge state={o.state} kind={o.label} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {result.rollback ? <p className="text-sm text-ink-muted">Rollback path: {result.rollback}</p> : null}
    </div>
  );
}
