"use client";

import type { PrincipalItem, RecoveryProgress } from "@zoiko/api-client";
import { Badge, Button, Card, CopyId, DateTime, Dialog, KeyValues, RiskBadge, StatusBadge, TextField } from "@zoiko/ui";
import { LifeBuoy, ShieldX } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { OneTimeValue } from "./one-time-value";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function ProgressView({ p }: { p: RecoveryProgress }) {
  return (
    <KeyValues
      items={[
        ["Recovery case", <CopyId key="id" value={p.recovery_id} label="recovery ID" />],
        ["Class", <Badge key="c" tone="info">{p.recovery_class}</Badge>],
        ["Status", <StatusBadge key="s" state={p.status} kind="Recovery status" />],
        ["Cooling off until", <DateTime key="co" value={p.cooling_off_until ?? null} />],
        [
          "Still required",
          p.missing.length ? (
            <ul key="m" className="flex flex-wrap gap-1">
              {p.missing.map((m) => (
                <li key={m}>
                  <Badge tone="warn">{m}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            "Nothing"
          ),
        ],
      ]}
    />
  );
}

function AssistDialog({ principal, isOpen, onClose }: { principal: PrincipalItem; isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // The handle is shown once; kept only in component state, never in the query cache.
  const [progress, setProgress] = useState<RecoveryProgress | null>(null);

  function close() {
    if (pending) return;
    setError(null);
    setProgress(null);
    onClose();
  }

  async function open() {
    setPending(true);
    setError(null);
    try {
      setProgress(await protect(() => api.recoveryAdmin.assist(principal.principal_id)));
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !progress && close()}
      isDismissable={!progress}
      title={progress ? "Assisted recovery opened" : "Open assisted recovery?"}
      size="lg"
      footer={
        progress ? undefined : (
          <>
            <Button variant="secondary" isDisabled={pending} onPress={close}>
              Cancel
            </Button>
            <Button pending={pending} onPress={() => void open()}>
              Open recovery case
            </Button>
          </>
        )
      }
    >
      {progress ? (
        <div className="space-y-5">
          <ProgressView p={progress} />
          {progress.recovery_handle ? (
            <OneTimeValue
              label="Recovery handle"
              value={progress.recovery_handle}
              acknowledgement="I have passed the handle to the account holder through a verified channel."
              onDone={close}
            >
              Give it to the account holder out of band. The handle is not proof of identity: they must still pass every listed gate
              (re-proofing, approval, cooling-off) before they can enrol a new passkey.
            </OneTimeValue>
          ) : (
            <div className="flex justify-end">
              <Button onPress={close}>Done</Button>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3 text-sm text-ink-muted">
          <RiskBadge risk="R2" />
          <p>
            Opens an RC-3 support-assisted recovery case for <code className="font-mono text-xs">{principal.principal_id}</code>. The
            account holder continues it with a one-time recovery handle that you hand over outside this console.
          </p>
          <p>You cannot open a recovery for your own account. Accounts under an RC-4 lockbox follow their lockbox policy instead.</p>
          {error ? <ApiErrorView error={error} /> : null}
        </div>
      )}
    </Dialog>
  );
}

function DenyDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [id, setId] = useState("");
  const [reason, setReason] = useState("");
  const [invalid, setInvalid] = useState<string | undefined>();
  const deny = useCellMutation((api, v: { id: string; reason: string }) => api.recoveryAdmin.deny(v.id, v.reason), {
    success: "Recovery case denied",
    onSuccess: () => close(),
  });

  function close() {
    setId("");
    setReason("");
    setInvalid(undefined);
    deny.reset();
    onClose();
  }

  function submit() {
    if (!UUID_RE.test(id.trim())) return setInvalid("Enter the recovery case UUID.");
    setInvalid(undefined);
    deny.mutate({ id: id.trim(), reason: reason.trim() });
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && !deny.isPending && close()}
      title="Deny a recovery case"
      size="md"
      role="alertdialog"
      footer={
        <>
          <Button variant="secondary" isDisabled={deny.isPending} onPress={close}>
            Cancel
          </Button>
          <Button variant="danger" pending={deny.isPending} isDisabled={reason.trim().length < 3 || !id.trim()} onPress={submit}>
            Deny recovery
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-ink-muted">
          Stops the recovery at once; its handle and any enrolment ticket stop working. Use it when a recovery looks fraudulent or the
          account holder did not ask for it.
        </p>
        <TextField label="Recovery case ID" isRequired value={id} onChange={setId} mono errorMessage={invalid} />
        <TextField
          label="Reason"
          isRequired
          multiline
          rows={3}
          value={reason}
          onChange={setReason}
          description="3–200 characters. Recorded with the decision."
        />
        {deny.error ? <ApiErrorView error={deny.error} /> : null}
      </div>
    </Dialog>
  );
}

export function RecoveryCard({ principal }: { principal: PrincipalItem }) {
  const [assisting, setAssisting] = useState(false);
  const [denying, setDenying] = useState(false);
  return (
    <Card
      title="Account recovery"
      description="Support-assisted recovery (RC-3) for a principal who has lost every passkey and recovery code."
      actions={
        <>
          <Button size="sm" variant="secondary" icon={<ShieldX aria-hidden className="size-4" />} onPress={() => setDenying(true)}>
            Deny a case
          </Button>
          <Button size="sm" icon={<LifeBuoy aria-hidden className="size-4" />} onPress={() => setAssisting(true)}>
            Open assisted recovery
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-muted">
        An assisted case still needs identity re-proofing and a second administrator&apos;s approval before the account holder can enrol a
        new passkey. Completing it revokes every older session. The cell does not list open cases; deny one by its ID.
      </p>
      <AssistDialog principal={principal} isOpen={assisting} onClose={() => setAssisting(false)} />
      <DenyDialog isOpen={denying} onClose={() => setDenying(false)} />
    </Card>
  );
}
