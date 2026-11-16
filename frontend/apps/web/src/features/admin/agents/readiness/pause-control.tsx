"use client";

import { Banner, Button, Dialog, Select } from "@zoiko/ui";
import { PauseCircle } from "lucide-react";
import { useState } from "react";

const REASONS = [
  { id: "OPERATOR_DECISION", label: "Operator decision" },
  { id: "SECURITY_CONCERN", label: "Security concern" },
  { id: "SPONSOR_CHANGE", label: "Sponsor change" },
  { id: "POLICY_CHANGE", label: "Policy change" },
];

/** The kill switch for this agent. It asks first, says what it does, and leaves the decision and the audit record to the server. */
export function PauseAgentControl({
  paused,
  pending = false,
  error,
  onPause,
  onResume,
}: {
  paused: boolean;
  pending?: boolean;
  /** The server's refusal, drawn inside the dialog so it is not missed. */
  error?: React.ReactNode;
  onPause: (reason: string) => void;
  onResume: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("OPERATOR_DECISION");
  if (paused) {
    return (
      <Button variant="secondary" size="lg" pending={pending} onPress={onResume}>
        Resume future runs
      </Button>
    );
  }
  return (
    <>
      <Button variant="danger-outline" size="lg" icon={<PauseCircle aria-hidden className="size-4" />} onPress={() => setOpen(true)}>
        Pause future runs
      </Button>
      <Dialog
        isOpen={open}
        onOpenChange={setOpen}
        role="alertdialog"
        size="md"
        title="Pause future runs?"
        description="This is recorded in the audit trail with your name and the reason."
        footer={
          <>
            <Button variant="secondary" size="lg" isDisabled={pending} onPress={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="lg"
              pending={pending}
              onPress={() => {
                onPause(reason);
                setOpen(false);
              }}
            >
              Pause agent
            </Button>
          </>
        }
      >
        <div className="space-y-4 text-sm text-ink-muted">
          <p>
            The approval is withdrawn and queued runs are canceled. A run that is already going stops at its next policy check. The agent cannot run again until it is approved
            again. No access permission changes.
          </p>
          <Select label="Reason" options={REASONS} value={reason} onChange={setReason} />
          {error ? <Banner tone="danger" title="Not paused" live="assertive">{error}</Banner> : null}
        </div>
      </Dialog>
    </>
  );
}
