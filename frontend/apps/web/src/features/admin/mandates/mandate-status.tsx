"use client";

import type { Mandate, MandateStatus } from "@zoiko/api-client";
import { Button, CopyId, Dialog, Select } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { RISK_OPTIONS } from "../authority/shared";

export const MANDATES_KEY = ["mandates"];

export type Move = "activate" | "pause" | "revoke";

export function allowedMoves(s: MandateStatus): Move[] {
  switch (s) {
    case "PENDING_APPROVAL":
      return ["activate", "revoke"];
    case "ACTIVE":
      return ["pause", "revoke"];
    case "PAUSED":
      return ["activate", "revoke"];
    case "EXHAUSTED":
      return ["revoke"];
    default:
      return [];
  }
}

/** Key budget terms as text; amounts stay the decimal strings the cell returned. */
export function budgetSummary(m: Mandate): string {
  const parts: string[] = [];
  if (m.value_limit !== null) parts.push(`${m.value_limit} ${m.currency ?? ""}`.trim());
  if (m.count_limit !== null) parts.push(`${m.count_limit} uses`);
  if (m.data_limit_bytes !== null) parts.push(`${m.data_limit_bytes} B`);
  return parts.length ? parts.join(" · ") : "Unbudgeted";
}

const COPY: Record<Move, { title: string; verb: string; body: string }> = {
  activate: {
    title: "Activate this mandate?",
    verb: "Activate",
    body: "The executor can start acting within the mandate's terms. The cell re-checks the source grant's chain at the risk class you choose; if the grant no longer supports the mandate, activation is refused.",
  },
  pause: {
    title: "Pause this mandate?",
    verb: "Pause",
    body: "The executor stops being able to reserve budget or act under the mandate until it is activated again. Open reservations can still be committed or released.",
  },
  revoke: {
    title: "Revoke this mandate?",
    verb: "Revoke",
    body: "The mandate ends permanently and the executor loses this authority immediately. This cannot be undone; create a new mandate instead.",
  },
};

export function StatusChangeDialog({ mandate, move, onClose }: { mandate: Mandate; move: Move; onClose: () => void }) {
  const [risk, setRisk] = useState("3");
  const copy = COPY[move];
  const run = useCellMutation(
    (api, v: { id: string; move: Move; risk: number }) =>
      v.move === "activate" ? api.mandates.activate(v.id, v.risk) : v.move === "pause" ? api.mandates.pause(v.id) : api.mandates.revoke(v.id),
    {
      invalidate: [MANDATES_KEY],
      success: (r) => `Mandate is now ${r.status.toLowerCase().replace(/_/g, " ")}`,
      onSuccess: onClose,
    },
  );

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !run.isPending && onClose()}
      title={copy.title}
      size="sm"
      role="alertdialog"
      footer={
        <>
          <Button variant="secondary" isDisabled={run.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button
            variant={move === "revoke" ? "danger" : "primary"}
            pending={run.isPending}
            onPress={() => run.mutate({ id: mandate.mandate_id, move, risk: Number(risk) })}
          >
            {copy.verb} mandate
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-ink-muted">
        <p>
          Mandate <CopyId value={mandate.mandate_id} label="mandate" />
        </p>
        <p>{copy.body}</p>
        {move === "activate" ? (
          <Select
            label="Check the source grant at risk class"
            value={risk}
            onChange={setRisk}
            options={RISK_OPTIONS}
            description="Use the highest risk class the executor's actions will need."
          />
        ) : null}
        {run.error ? <ApiErrorView error={run.error} /> : null}
      </div>
    </Dialog>
  );
}
