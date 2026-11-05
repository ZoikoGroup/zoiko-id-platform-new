"use client";

import { ApiError, type RecoveryProgress } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card, CopyId, DateTime, KeyValues, StatusBadge, TextField } from "@zoiko/ui";
import { ArrowLeft, Check, Copy, Fingerprint, LifeBuoy, RefreshCw } from "lucide-react";
import { useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { useRuntime } from "@/lib/runtime";

import { EmailRecovery } from "./email-recovery";
import { browserSupportsWebAuthn, completeAppSession, createPasskey, getPasskey, passkeyErrorMessage } from "@/lib/webauthn";

const noSubscribe = () => () => {};

/** The cell's recovery errors carry a generic message; say what each one means. */
const RECOVERY_ERRORS: Record<string, string> = {
  RECOVERY_CODE_INVALID: "That recovery code isn't valid, or it has already been used. Check it and try again.",
  RECOVERY_NOT_FOUND: "We couldn't find that recovery. Check the recovery ID and recovery key.",
  RECOVERY_NOT_ALLOWED: "This account can't be recovered this way. Contact your administrator.",
  RECOVERY_CLOSED: "This recovery has already finished or was stopped. Start again with a new recovery code.",
  RECOVERY_EXPIRED: "This recovery expired. Start again with a new recovery code.",
  RECOVERY_NOT_READY: "This recovery isn't ready yet. Check its status again.",
  LOCKBOX_POLICY_MISSING: "This account's recovery policy changed. Contact your administrator.",
  ENROLLMENT_TICKET_INVALID: "The time to create your new passkey ran out. Check the recovery status again to get more time.",
  CLIENT_UNKNOWN: "Recovery isn't available from this app. Contact your administrator.",
};

function recoveryErrorMessage(e: unknown): string {
  const known = e instanceof ApiError ? RECOVERY_ERRORS[e.code] : undefined;
  if (known) return known;
  return passkeyErrorMessage(e);
}

/** Codes are issued as XXXXX-XXXXX-XXXXX-XXXXX; accept any spacing, dashes or case. */
function normalizeCode(v: string): string {
  const raw = v.replace(/[\s-]/g, "").toUpperCase();
  return raw.length === 20 ? (raw.match(/.{5}/g) ?? []).join("-") : raw;
}

const STATUS_COPY: Record<string, { title: string; body: string }> = {
  PENDING_REPROOF: {
    title: "Verify your identity",
    body: "Because of how this account is protected, a recovery code alone isn't enough. Your administrator or support team will arrange an identity verification with you. Once it succeeds, check the status here.",
  },
  PENDING_APPROVAL: {
    title: "Waiting for approval",
    body: "A second administrator must approve this recovery. When they have, enter the approval request ID they give you and check the status.",
  },
  COOLING_OFF: {
    title: "Waiting period",
    body: "For your protection, recovery waits for a set time before you can create a new passkey. The account owner is notified and can stop it. Come back after the time below.",
  },
  DENIED: {
    title: "Recovery stopped",
    body: "This recovery was denied or cancelled. If you still need access, contact your administrator.",
  },
};

const MISSING_COPY: Record<string, string> = {
  REPROOF_REQUIRED: "Identity verification",
  SECOND_ADMIN_APPROVAL_REQUIRED: "Approval by a second administrator",
  DESIGNATED_APPROVERS_REQUIRED: "Approval by this account's designated approvers",
  COOLING_OFF: "Waiting period to finish",
};

type Pending = { progress: RecoveryProgress; handle: string; showHandle: boolean };
type Step =
  | { kind: "code" }
  | { kind: "resume" }
  | ({ kind: "pending" } & Pending)
  | { kind: "ready"; ticket: string; recoveryId: string; handle: string };

function CopyValue({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-ink">{label}</div>
      <div className="flex items-stretch gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto rounded-control border border-border-strong bg-canvas px-3 py-2 font-mono text-xs text-ink">
          {value}
        </code>
        <Button
          variant="secondary"
          size="sm"
          className="h-auto"
          aria-label={`Copy ${label}`}
          icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
          onPress={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

/** Account recovery: code -> the cell's gates (re-proofing, approval, cooling-off) -> a 15-minute
 * enrollment ticket -> a new passkey -> signed in. State lives only in this component. */
export function Recover() {
  const { flow } = useRuntime();
  const supported = useSyncExternalStore(noSubscribe, browserSupportsWebAuthn, () => true);
  const [step, setStep] = useState<Step>({ kind: "code" });
  const [code, setCode] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [resumeHandle, setResumeHandle] = useState("");
  const [approvalId, setApprovalId] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);

  function advance(progress: RecoveryProgress, handle: string, fresh: boolean) {
    if (progress.status === "READY" && progress.enrollment_ticket) {
      setStep({ kind: "ready", ticket: progress.enrollment_ticket, recoveryId: progress.recovery_id, handle });
    } else {
      setStep({ kind: "pending", progress, handle, showHandle: fresh });
    }
  }

  async function run(fn: () => Promise<void>) {
    setPending(true);
    setFailure(null);
    try {
      await fn();
    } catch (e) {
      setFailure(recoveryErrorMessage(e));
    } finally {
      setPending(false);
    }
  }

  function startWithCode(e: FormEvent) {
    e.preventDefault();
    const value = normalizeCode(code);
    if (value.length < 16) {
      setFailure("Enter the full recovery code, for example ABCDE-FGHJK-LMNPQ-RSTUV.");
      return;
    }
    void run(async () => {
      const progress = await flow.startRecovery(value);
      setCode("");
      advance(progress, progress.recovery_handle ?? "", true);
    });
  }

  function resume(e: FormEvent) {
    e.preventDefault();
    const id = resumeId.trim();
    const handle = resumeHandle.trim();
    if (!id || handle.length < 16) {
      setFailure("Enter the recovery ID and the recovery key you saved when you started.");
      return;
    }
    void run(async () => {
      const progress = await flow.continueRecovery(id, handle, approvalId.trim() || undefined);
      advance(progress, handle, false);
    });
  }

  function checkAgain(p: Pending) {
    void run(async () => {
      const progress = await flow.continueRecovery(p.progress.recovery_id, p.handle, approvalId.trim() || undefined);
      advance(progress, p.handle, p.showHandle);
    });
  }

  function createNewPasskey(ticket: string) {
    void run(async () => {
      if (!registered) {
        const options = await flow.registrationOptions({ enrollment_ticket: ticket });
        // Registering with the recovery ticket completes the recovery and ends every older session.
        await flow.registrationVerify(await createPasskey(options), "Recovered passkey");
        setRegistered(true);
      }
      const options = await flow.passkeyOptions();
      await flow.passkeyVerify(await getPasskey(options));
      await completeAppSession();
      window.location.assign("/account");
    });
  }

  /** The ticket lasts 15 minutes; a READY recovery hands out a fresh one when continued. */
  function renewTicket(recoveryId: string, handle: string) {
    void run(async () => {
      const progress = await flow.continueRecovery(recoveryId, handle);
      advance(progress, handle, false);
    });
  }

  function startOver() {
    setStep({ kind: "code" });
    setApprovalId("");
    setResumeId("");
    setResumeHandle("");
    setFailure(null);
    setRegistered(false);
  }

  const failureBanner = failure ? (
    <Banner tone="danger" live="assertive" title={step.kind === "ready" && registered ? "Couldn't sign you in" : "Couldn't continue recovery"}>
      {failure}
    </Banner>
  ) : null;

  if (step.kind === "ready") {
    return (
      <div className="space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Create a new passkey</h1>
          <p className="mt-1 text-sm text-ink-muted">Your recovery is approved. You have 15 minutes to create a new passkey.</p>
        </div>
        {registered && !failure ? (
          <Banner tone="info" title="Passkey created">
            Confirm it once more to sign in.
          </Banner>
        ) : null}
        {failureBanner}
        {!supported ? (
          <Banner tone="warn" title="This browser can't use passkeys">
            Use a current version of Chrome, Edge, Safari or Firefox.
          </Banner>
        ) : null}
        <Card>
          <div className="space-y-3">
            <p className="text-sm text-ink-muted">
              Creating it finishes the recovery and signs your account out everywhere else. Passkeys you could no longer use may be
              marked as lost.
            </p>
            <Button
              size="lg"
              className="w-full"
              pending={pending}
              isDisabled={!supported}
              icon={<Fingerprint aria-hidden className="size-5" />}
              onPress={() => createNewPasskey(step.ticket)}
            >
              {registered ? "Sign in with your new passkey" : "Create passkey"}
            </Button>
            {failure && !registered && step.handle ? (
              <Button
                variant="ghost"
                size="sm"
                className="w-full"
                isDisabled={pending}
                icon={<RefreshCw aria-hidden className="size-4" />}
                onPress={() => renewTicket(step.recoveryId, step.handle)}
              >
                Ran out of time? Get a new 15 minutes
              </Button>
            ) : null}
          </div>
        </Card>
      </div>
    );
  }

  if (step.kind === "pending") {
    const { progress } = step;
    const copy = STATUS_COPY[progress.status] ?? { title: "Recovery in progress", body: "Check the status again to continue." };
    const closed = progress.status === "DENIED";
    return (
      <div className="space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">{copy.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">{copy.body}</p>
        </div>
        {failureBanner}

        {step.showHandle && !closed ? (
          <Banner tone="warn" title="Save these to continue later">
            Recovery can take days. You need both values to come back to this recovery; the recovery key is shown only once.
          </Banner>
        ) : null}

        <Card>
          <div className="space-y-4">
            <KeyValues
              columns={1}
              items={[
                ["Status", <StatusBadge key="s" state={progress.status} kind="Recovery" />],
                ["Recovery ID", <CopyId key="r" value={progress.recovery_id} short={false} label="recovery" />],
                ...(progress.missing.length
                  ? ([["Still needed", progress.missing.map((m) => MISSING_COPY[m] ?? m).join(" · ")]] as [string, string][])
                  : []),
                ...(progress.cooling_off_until
                  ? ([["Ready after", <DateTime key="c" value={progress.cooling_off_until} />]] as [string, ReactNode][])
                  : []),
              ]}
            />
            {step.showHandle && !closed ? <CopyValue label="Recovery key" value={step.handle} /> : null}
            {progress.status === "PENDING_APPROVAL" ? (
              <TextField
                label="Approval request ID"
                value={approvalId}
                onChange={setApprovalId}
                mono
                description="Given to you by the administrator who approved your recovery."
              />
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" icon={<ArrowLeft aria-hidden className="size-4" />} isDisabled={pending} onPress={startOver}>
                Start over
              </Button>
              {!closed ? (
                <Button pending={pending} icon={<RefreshCw aria-hidden className="size-4" />} onPress={() => checkAgain(step)}>
                  Check status
                </Button>
              ) : null}
            </div>
          </div>
        </Card>
        <p className="text-center text-xs text-ink-subtle">
          Didn&apos;t start this? Sign in with your passkey and contact your administrator. A recovery you leave unfinished expires on its
          own.
        </p>
      </div>
    );
  }

  if (step.kind === "resume") {
    return (
      <div className="space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Continue a recovery</h1>
          <p className="mt-1 text-sm text-ink-muted">Enter the recovery ID and recovery key you saved when you started.</p>
        </div>
        {failureBanner}
        <Card>
          <form onSubmit={resume} className="space-y-4">
            <TextField label="Recovery ID" value={resumeId} onChange={setResumeId} mono isRequired autoComplete="off" />
            <TextField label="Recovery key" value={resumeHandle} onChange={setResumeHandle} mono isRequired autoComplete="off" type="password" />
            <TextField
              label="Approval request ID (if you have one)"
              value={approvalId}
              onChange={setApprovalId}
              mono
              autoComplete="off"
            />
            <Button type="submit" size="lg" className="w-full" pending={pending}>
              Continue
            </Button>
          </form>
        </Card>
        <div className="flex justify-center">
          <Button variant="ghost" size="sm" icon={<ArrowLeft aria-hidden className="size-4" />} onPress={startOver}>
            Use a recovery code instead
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">Recover your account</h1>
        <p className="mt-1 text-sm text-ink-muted">Lost your passkey or forgot how to sign in? Get an emailed link, or use a recovery code you saved, to set up a new one.</p>
      </div>
      {failureBanner}
      <EmailRecovery />
      <div className="flex items-center gap-3 py-1 text-xs text-ink-subtle">
        <span className="h-px flex-1 bg-border" />
        or use a recovery code
        <span className="h-px flex-1 bg-border" />
      </div>
      <Card>
        <form onSubmit={startWithCode} className="space-y-4">
          <TextField
            label="Recovery code"
            value={code}
            onChange={setCode}
            mono
            isRequired
            autoComplete="off"
            placeholder="XXXXX-XXXXX-XXXXX-XXXXX"
            description="Each code works once. Dashes and spaces are optional."
          />
          <Button type="submit" size="lg" className="w-full" pending={pending} icon={<LifeBuoy aria-hidden className="size-5" />}>
            Continue
          </Button>
        </form>
      </Card>
      <div className="flex flex-col items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onPress={() => {
            setFailure(null);
            setStep({ kind: "resume" });
          }}
        >
          Already started a recovery? Continue it
        </Button>
        <ButtonLink href="/sign-in" variant="ghost" size="sm">
          Found your passkey? Sign in
        </ButtonLink>
      </div>
    </div>
  );
}
