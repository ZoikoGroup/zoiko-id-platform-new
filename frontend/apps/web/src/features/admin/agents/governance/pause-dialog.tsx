"use client";

import { Banner, Button, Dialog, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { GOV_KEY } from "./logic";

const CONFIRM_WORD = "PAUSE";

type Result = { case_id: string; generation: number; agents_suspended?: number; already_frozen: boolean };

/** The emergency stop. It asks for a reason and a typed confirmation, needs no approval first, and says "stopped" only after the server did. */
export function PauseDialog({ isOpen, onClose, agentId, agentName }: { isOpen: boolean; onClose: () => void; agentId?: string; agentName?: string }) {
  const [reason, setReason] = useState("");
  const [severity, setSeverity] = useState<"P0" | "P1" | "P2">("P1");
  const [typed, setTyped] = useState("");
  const [done, setDone] = useState<Result | null>(null);
  const company = !agentId;

  const pause = useCellMutation<void, Result>(
    (api) => (agentId ? api.agentGovernance.pauseAgent(agentId, { reason: reason.trim(), severity }) : api.agentGovernance.pauseCompany({ reason: reason.trim(), severity })),
    { invalidate: [GOV_KEY, ["admin", "agents"]], onSuccess: (r) => setDone(r) },
  );

  function close() {
    if (pause.isPending) return;
    setReason("");
    setTyped("");
    setDone(null);
    pause.reset();
    onClose();
  }

  const ready = reason.trim().length >= 3 && typed === CONFIRM_WORD;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      role="alertdialog"
      title={company ? "Pause every agent in the company" : `Pause ${agentName ?? "this agent"}`}
      description="Stops new work now and cancels running work. No approval is asked for first; getting back needs two independent people."
      footer={
        done ? (
          <Button onPress={close}>Close</Button>
        ) : (
          <>
            <Button variant="secondary" isDisabled={pause.isPending} onPress={close}>Cancel</Button>
            <Button variant="danger" isDisabled={!ready} pending={pause.isPending} onPress={() => pause.mutate()}>
              {company ? "Pause the company" : "Pause agent"}
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        {done ? (
          <Banner tone={done.already_frozen ? "info" : "ok"} title={done.already_frozen ? "Already paused" : "Stopped, confirmed by the server"} live="polite">
            Emergency case generation {done.generation}.{typeof done.agents_suspended === "number" ? ` ${done.agents_suspended} agent(s) suspended.` : ""} New calls are refused at the
            next check.
          </Banner>
        ) : (
          <>
            <TextField label="Reason" isRequired value={reason} onChange={setReason} multiline rows={3} description="Written to the evidence record. Do not put personal data here." />
            <Select
              label="Severity"
              options={[
                { id: "P0", label: "P0: active harm or data exposure" },
                { id: "P1", label: "P1: suspicious behaviour" },
                { id: "P2", label: "P2: precaution or drill" },
              ]}
              value={severity}
              onChange={(v) => setSeverity(v as "P0" | "P1" | "P2")}
            />
            <TextField label={`Type ${CONFIRM_WORD} to confirm`} value={typed} onChange={setTyped} autoComplete="off" />
          </>
        )}
        {pause.error ? <ApiErrorView error={pause.error} /> : null}
      </div>
    </Dialog>
  );
}
