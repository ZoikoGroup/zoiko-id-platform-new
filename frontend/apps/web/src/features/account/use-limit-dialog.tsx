"use client";

import type { MyMandate } from "@zoiko/api-client";
import { Button, Dialog, Select, TextField, humanize } from "@zoiko/ui";
import { useState } from "react";

import { MANDATES_KEY } from "@/features/admin/mandates/mandate-status";
import { ApiErrorView, newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/cell";

import { checkAmount, leftAfter, transactionContext } from "./use-limit";

/**
 * "Use my limit": the person a limit was given to spends part of it. They pick what for, type the amount and a reference, and the cell holds
 * then books it in one step. Nothing here shows anyone else's limit, usage or name: the balance is this limit's own.
 */
export function UseLimitDialog({ mandate: m, onClose }: { mandate: MyMandate; onClose: () => void }) {
  const [action, setAction] = useState(m.allowed_actions[0] ?? "");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [key] = useState(newIdempotencyKey); // one attempt, one key: pressing twice cannot spend twice

  const balance = useCellQuery([...MANDATES_KEY, "balance", m.mandate_id], (api) => api.mandates.balance(m.mandate_id));
  const remaining = balance.data?.remaining.value ?? null;

  const use = useCellMutation(
    async (api, v: { action: string; amount: string; reference: string }) => {
      const held = await api.mandates.reserve(
        m.mandate_id,
        { action: v.action, tool: null, transaction_context: transactionContext(v.reference, m.mandate_id), value: { amount: v.amount, currency: m.currency ?? "" }, count: 0, data_bytes: 0 },
        key,
      );
      try {
        return await api.mandates.commit(held.reservation_id, { recheck_passed: true, execution_id: null });
      } catch (e) {
        await api.mandates.release(held.reservation_id).catch(() => undefined); // never leave the amount held if it could not be booked
        throw e;
      }
    },
    { invalidate: [MANDATES_KEY], success: "Limit used", onSuccess: onClose },
  );

  function submit() {
    if (!action) return setProblem("Choose what this is for.");
    if (!reference.trim()) return setProblem("Enter a reference, such as an invoice number.");
    const checked = checkAmount(amount, { perTransaction: m.per_transaction_value_limit, remaining, currency: m.currency });
    if (!checked.ok) return setProblem(checked.reason);
    setProblem(null);
    use.mutate({ action, amount: checked.amount, reference });
  }

  const typed = checkAmount(amount, { perTransaction: m.per_transaction_value_limit, remaining, currency: m.currency });
  const left = typed.ok ? leftAfter(remaining, typed.amount) : null;

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !use.isPending && onClose()}
      title="Use my limit"
      size="sm"
      footer={
        <>
          <Button variant="secondary" isDisabled={use.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={use.isPending} onPress={submit}>
            Approve and use
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-ink-muted">
          {remaining !== null ? (
            <>
              You have <span className="tabular font-mono text-ink">{`${remaining} ${m.currency ?? ""}`.trim()}</span> left on this limit.
            </>
          ) : (
            "This takes part of your limit and records it."
          )}
        </p>
        <Select label="What is it for?" isRequired value={action} onChange={setAction} options={m.allowed_actions.map((a) => ({ id: a, label: humanize(a) }))} />
        <TextField
          label={`Amount${m.currency ? ` (${m.currency})` : ""}`}
          isRequired
          value={amount}
          onChange={setAmount}
          inputMode="decimal"
          mono
          placeholder="0.00"
          description={left !== null ? `You will have ${left} left.` : m.per_transaction_value_limit !== null ? `Up to ${m.per_transaction_value_limit} each time.` : undefined}
        />
        <TextField label="Reference" isRequired value={reference} onChange={setReference} description="What this is for, for example an invoice number. It is kept with the record." />
        {problem ? (
          <p role="alert" className="text-danger-fg">
            {problem}
          </p>
        ) : null}
        {use.error ? <ApiErrorView error={use.error} title="Nothing was used" /> : null}
      </div>
    </Dialog>
  );
}
