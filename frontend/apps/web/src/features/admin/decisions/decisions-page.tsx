"use client";

import type { EvaluationRequest, EvaluationResult } from "@zoiko/api-client";
import { Button, Card, EmptyState, FieldGroup, PageHeader, RelativeTime, TextField, WithRail } from "@zoiko/ui";
import { History, Play, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, jsonInput, useCellMutation } from "@/lib/cell";

import { FormProblem, PrincipalField } from "../authority/shared";
import { DecisionResult, OutcomeBadge } from "./decision-result";

/** A few common catalogued actions as shortcuts; any catalogued name can be typed. */
const COMMON_ACTIONS = ["application.access", "resource.write", "invoice.approve", "payment.release", "purchase.create", "tool.execute", "data.sensitive.read"];

const CONTEXT_TEMPLATE = `{
  "purpose": null,
  "risk_class": "R2",
  "represented_principal_id": null,
  "mandate_id": null,
  "tool_id": null,
  "transaction_context_id": null,
  "value": { "amount": "100.00", "currency": "USD" }
}`;

type Entry = { id: number; at: string; request: EvaluationRequest; result: EvaluationResult };

export function DecisionsPage() {
  const [subjectType, setSubjectType] = useState("principal");
  const [subjectId, setSubjectId] = useState("");
  const [subjectProps, setSubjectProps] = useState("");
  const [action, setAction] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [resourceProps, setResourceProps] = useState("");
  const [context, setContext] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  // Session-only history: kept in component state, gone on reload; never written to storage.
  const [history, setHistory] = useState<Entry[]>([]);
  const [shown, setShown] = useState<number | null>(null);

  const evaluate = useCellMutation((api, body: EvaluationRequest) => api.decisions.evaluate(body), {
    onSuccess: (result, request) => {
      const entry = { id: Date.now(), at: new Date().toISOString(), request, result };
      setHistory((h) => [entry, ...h].slice(0, 25));
      setShown(entry.id);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!subjectType.trim() || !subjectId.trim()) return setProblem("Enter the subject's type and ID.");
    if (!action.trim()) return setProblem("Enter an action name.");
    if (!resourceType.trim() || !resourceId.trim()) return setProblem("Enter the resource's type and ID.");
    let body: EvaluationRequest;
    try {
      const sp = jsonInput(subjectProps, "Subject properties");
      const rp = jsonInput(resourceProps, "Resource properties");
      const ctx = jsonInput(context, "Context");
      body = {
        subject: { type: subjectType.trim(), id: subjectId.trim(), ...(sp ? { properties: sp } : {}) },
        action: { name: action.trim() },
        resource: { type: resourceType.trim(), id: resourceId.trim(), ...(rp ? { properties: rp } : {}) },
        ...(ctx ? { context: ctx as EvaluationRequest["context"] } : {}),
      };
    } catch (err) {
      return setProblem(err instanceof Error ? err.message : "Invalid JSON.");
    }
    setProblem(null);
    evaluate.mutate(body);
  }

  const current = history.find((h) => h.id === shown) ?? null;

  return (
    <>
      <PageHeader
        icon={ShieldCheck}
        title="Access decisions"
        description="Ask the policy decision point exactly what an enforcement point would ask (AuthZEN evaluation) and see the outcome with its evidence."
      />
      <WithRail
        rail={
          <Card title="Recent evaluations" description="This session only; cleared when you leave the page." padded={false}>
            {history.length === 0 ? (
              <EmptyState icon={History} title="No evaluations yet" compact>
                Results you run appear here.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border" aria-label="Recent evaluations">
                {history.map((h) => (
                  <li key={h.id}>
                    <button
                      type="button"
                      onClick={() => setShown(h.id)}
                      aria-current={h.id === shown ? "true" : undefined}
                      className="flex w-full flex-col items-start gap-1 px-4 py-3 text-start outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 aria-[current]:bg-brand-50/60"
                    >
                      <span className="flex w-full items-center justify-between gap-2">
                        <OutcomeBadge result={h.result} />
                        <span className="text-xs text-ink-subtle">
                          <RelativeTime value={h.at} />
                        </span>
                      </span>
                      <span className="w-full truncate font-mono text-xs text-ink">{h.request.action.name}</span>
                      <span className="w-full truncate font-mono text-2xs text-ink-subtle">
                        {h.request.resource.type}:{h.request.resource.id}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        }
      >
        <Card title="Evaluation request">
          <form onSubmit={submit} className="space-y-6" aria-label="Evaluation request">
            <FieldGroup legend="Subject" description="Who is asking to act; for agents, the executor principal.">
              <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
                <TextField label="Type" isRequired value={subjectType} onChange={setSubjectType} mono />
                <PrincipalField label="ID" isRequired value={subjectId} onChange={setSubjectId} />
              </div>
              <TextField label="Subject properties (JSON, optional)" multiline rows={2} mono value={subjectProps} onChange={setSubjectProps} />
            </FieldGroup>

            <FieldGroup legend="Action">
              <TextField label="Action name" isRequired value={action} onChange={setAction} mono description="A catalogued action name. Unknown names are denied with ACTION_UNKNOWN." />
              <div className="-mt-2 flex flex-wrap gap-1.5" role="group" aria-label="Common actions">
                {COMMON_ACTIONS.map((a) => (
                  <Button key={a} size="xs" variant={a === action ? "primary" : "secondary"} onPress={() => setAction(a)}>
                    {a}
                  </Button>
                ))}
              </div>
            </FieldGroup>

            <FieldGroup legend="Resource" description="Evaluated as type:id, matching grant and mandate resource scopes.">
              <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
                <TextField label="Type" isRequired value={resourceType} onChange={setResourceType} mono placeholder="invoice" />
                <TextField label="ID" isRequired value={resourceId} onChange={setResourceId} mono placeholder="inv_123" />
              </div>
              <TextField label="Resource properties (JSON, optional)" multiline rows={2} mono value={resourceProps} onChange={setResourceProps} />
            </FieldGroup>

            <FieldGroup legend="Context" description="Zoiko extensions: purpose, risk_class (R0–R3), represented_principal_id, mandate_id, tool_id, transaction_context_id and value (amount as a decimal string). Unknown keys are ignored.">
              <TextField label="Context (JSON, optional)" multiline rows={8} mono value={context} onChange={setContext} />
              <div className="-mt-2">
                <Button size="xs" variant="ghost" onPress={() => setContext(CONTEXT_TEMPLATE)}>
                  Insert template
                </Button>
              </div>
            </FieldGroup>

            <FormProblem>{problem}</FormProblem>
            {evaluate.error ? <ApiErrorView error={evaluate.error} title="The evaluation did not run" /> : null}
            <div className="flex justify-end">
              <Button type="submit" pending={evaluate.isPending} icon={<Play aria-hidden className="size-4" />}>
                Evaluate
              </Button>
            </div>
          </form>
        </Card>

        <Card title="Decision">
          {current ? (
            <DecisionResult request={current.request} result={current.result} />
          ) : (
            <EmptyState icon={ShieldCheck} title="No decision yet" compact>
              Fill in the request and choose Evaluate. Nothing is enforced here; the decision is only reported.
            </EmptyState>
          )}
        </Card>
      </WithRail>
    </>
  );
}
