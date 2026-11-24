"use client";

import type { EvaluationRequest, EvaluationResult } from "@zoiko/api-client";
import { Badge, Button, Card, Select, TextField, humanize } from "@zoiko/ui";
import { CheckCircle2, ShieldQuestion, XCircle } from "lucide-react";
import { useState } from "react";

import { useMe } from "@/features/admin/authority/shared";
import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { buildCheck, verdictOf } from "./check-action";
import { actionLabel } from "./my-permissions";

/**
 * "Check an action": ask whether the signed-in person may do something, before trying it. It only ever asks about themselves and changes nothing,
 * so a Member can see why something would be refused without involving anyone.
 */
export function CheckAction() {
  const me = useMe();
  const grants = useCellQuery(["my-grants"], (api) => api.authority.myGrants());
  const limits = useCellQuery(["mandates", "mine"], (api) => api.mandates.mine());

  // everything this person is given: their permissions, and the actions their limits cover
  const actions = [...new Set([...(grants.data?.items ?? []).flatMap((g) => g.actions ?? []), ...(limits.data?.items ?? []).flatMap((m) => m.allowed_actions)])];
  const active = (limits.data?.items ?? []).filter((m) => m.status === "ACTIVE" && m.i_am.includes("executor"));

  const [action, setAction] = useState("");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [mandateId, setMandateId] = useState("");
  const [answer, setAnswer] = useState<EvaluationResult | null>(null);

  const chosen = action || actions[0] || "";
  const mandate = active.find((m) => m.mandate_id === mandateId) ?? null;
  const ask = useCellMutation((api, body: EvaluationRequest) => api.decisions.evaluate(body), { onSuccess: setAnswer });
  const verdict = answer ? verdictOf(answer) : null;

  return (
    <Card title="Check an action" description="Ask whether you may do something before you try it. This only asks about you and changes nothing.">
      {actions.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <ShieldQuestion aria-hidden className="size-4" /> You have not been given anything to check yet.
        </p>
      ) : (
        <div className="space-y-3">
          <Select label="What do you want to do?" value={chosen} onChange={(v) => { setAction(v); setAnswer(null); }} options={actions.map((a) => ({ id: a, label: actionLabel(a) || humanize(a) }))} />
          {active.length ? (
            <Select
              label="Under which limit?"
              value={mandateId || "__none__"}
              onChange={(v) => { setMandateId(v === "__none__" ? "" : v); setAnswer(null); }}
              options={[{ id: "__none__", label: "No limit, just my permission" }, ...active.map((m) => ({ id: m.mandate_id, label: `Up to ${m.value_limit ?? "any"} ${m.currency ?? ""}`.trim() }))]}
            />
          ) : null}
          <TextField label={`Amount${mandate?.currency ? ` (${mandate.currency})` : " (optional)"}`} value={amount} onChange={(v) => { setAmount(v); setAnswer(null); }} inputMode="decimal" mono placeholder="0.00" isDisabled={!mandate?.currency} />
          <TextField label="Reference (optional)" value={reference} onChange={setReference} description="For example an invoice number." />
          <Button
            pending={ask.isPending}
            isDisabled={!me || !chosen}
            onPress={() => me && ask.mutate(buildCheck({ me, action: chosen, reference, amount, currency: mandate?.currency ?? null, mandateId: mandate?.mandate_id ?? null }))}
          >
            Check
          </Button>
          {ask.error ? <ApiErrorView error={ask.error} title="The check could not be made" /> : null}
          {verdict ? (
            <div role="status" aria-live="polite" className="rounded-card border border-border p-3">
              <Badge tone={verdict.tone} icon={verdict.tone === "ok" ? CheckCircle2 : XCircle}>
                {verdict.title}
              </Badge>
              {verdict.why.length ? (
                <ul className="mt-2 list-disc space-y-1 ps-5 text-sm text-ink-muted">
                  {verdict.why.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}
