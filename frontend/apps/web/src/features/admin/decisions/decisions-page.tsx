"use client";

import type { EvaluationRequest, EvaluationResult } from "@zoiko/api-client";
import { Button, Card, EmptyState, FieldGroup, PageHeader, RelativeTime, Select, TextField, WithRail, type Option } from "@zoiko/ui";
import { History, Play, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, jsonInput, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { DECIMAL_RE, FormProblem, PrincipalField, shortId, useMe } from "../authority/shared";
import { DecisionResult, OutcomeBadge } from "./decision-result";

/** The actions most often asked about, in plain words, with the kind of thing each one applies to. */
const ACTIONS: { id: string; label: string; resource: string; money: boolean }[] = [
  { id: "payment.release", label: "Release a payment", resource: "payment", money: true },
  { id: "purchase.create", label: "Create a purchase", resource: "purchase", money: true },
  { id: "invoice.approve", label: "Approve an invoice", resource: "invoice", money: true },
  { id: "application.access", label: "Open an application", resource: "application", money: false },
  { id: "resource.write", label: "Change a record", resource: "record", money: false },
  { id: "data.sensitive.read", label: "Read sensitive data", resource: "data", money: false },
  { id: "tool.execute", label: "Run a tool", resource: "tool", money: false },
];
const ACTION_OPTIONS: Option[] = ACTIONS.map((a) => ({ id: a.id, label: a.label, description: a.id }));
const CURRENCIES: Option[] = ["GBP", "USD", "EUR"].map((c) => ({ id: c, label: c }));

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
  const me = useMe();
  const { confirmIdentity } = useRuntime();
  const [subjectType, setSubjectType] = useState("principal");
  const [subjectPicked, setSubjectPicked] = useState("");
  const subjectId = subjectPicked || me || ""; // asking about yourself unless you choose someone else
  const setSubjectId = setSubjectPicked;
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("GBP");
  const [mandateId, setMandateId] = useState("");
  const activeMandates = useCellQuery(["mandates", "list", "decision-picker"], (api) => api.mandates.list({ status: "ACTIVE", limit: 100 }));
  const mandateOptions: Option[] = [
    { id: "", label: "No mandate", description: "Decide on the person's own authority only." },
    ...(activeMandates.data?.items ?? []).map((m) => ({
      id: m.mandate_id,
      label: `Mandate ${shortId(m.mandate_id)}`,
      description: `${m.value_limit ? `up to ${m.value_limit} ${m.currency ?? ""}` : "no value limit"}${m.per_transaction_value_limit ? `, ${m.per_transaction_value_limit} each` : ""}`,
    })),
  ];
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
    if (!subjectType.trim() || !subjectId.trim()) return setProblem("Choose who is asking.");
    if (!action.trim()) return setProblem("Choose what they want to do.");
    if (!resourceType.trim() || !resourceId.trim()) return setProblem("Say what it applies to: its kind and a name or number.");
    if (amount.trim() && !DECIMAL_RE.test(amount.trim())) return setProblem("The amount must be a number such as 300.00.");
    let body: EvaluationRequest;
    try {
      const sp = jsonInput(subjectProps, "Subject properties");
      const rp = jsonInput(resourceProps, "Resource properties");
      const typed = jsonInput(context, "Context") as Record<string, unknown> | null;
      const ctx: Record<string, unknown> | null =
        typed || amount.trim() || mandateId
          ? {
              ...(typed ?? {}),
              ...(mandateId ? { mandate_id: mandateId } : {}),
              ...(amount.trim() ? { value: { amount: amount.trim(), currency } } : {}),
            }
          : null;
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
            <div className="grid gap-4 sm:grid-cols-2">
              <PrincipalField label="Who is asking" isRequired value={subjectId} onChange={setSubjectId} description="Yourself, unless you choose someone else." />
              <Select
                label="What do they want to do"
                isRequired
                value={action || null}
                placeholder="Choose…"
                onChange={(v) => {
                  setAction(v);
                  const next = ACTIONS.find((a) => a.id === v);
                  const prev = ACTIONS.find((a) => a.id === action);
                  if (next && (!resourceType || resourceType === prev?.resource)) setResourceType(next.resource);
                }}
                options={ACTION_OPTIONS}
              />
              <TextField label="Applies to: kind" isRequired value={resourceType} onChange={setResourceType} mono placeholder="payment" description="Filled in from the action." />
              <TextField label="Applies to: name or number" isRequired value={resourceId} onChange={setResourceId} mono placeholder="inv-001" description="Any reference, for example an invoice number." />
            </div>

            {ACTIONS.find((a) => a.id === action)?.money ? (
              <FieldGroup legend="Payment details" description="Only for actions that move money. Leave empty to ask without an amount.">
                <div className="grid gap-4 sm:grid-cols-3">
                  <TextField label="Amount" value={amount} onChange={setAmount} inputMode="decimal" mono placeholder="300.00" />
                  <Select label="Currency" value={currency} onChange={setCurrency} options={CURRENCIES} />
                  <Select label="Spending limit (mandate)" value={mandateId} onChange={setMandateId} options={mandateOptions} />
                </div>
              </FieldGroup>
            ) : null}

            <details className="rounded-card border border-border">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">
                Advanced <span className="font-normal text-ink-subtle">(rarely needed)</span>
              </summary>
              <div className="space-y-4 border-t border-border p-4">
                <TextField label="Subject type" value={subjectType} onChange={setSubjectType} mono />
                <TextField label="Subject properties (JSON, optional)" multiline rows={2} mono value={subjectProps} onChange={setSubjectProps} />
                <TextField label="Resource properties (JSON, optional)" multiline rows={2} mono value={resourceProps} onChange={setResourceProps} />
                <TextField
                  label="Extra context (JSON, optional)"
                  multiline
                  rows={6}
                  mono
                  value={context}
                  onChange={setContext}
                  description="For example purpose, risk_class, represented_principal_id or tool_id. Amount and mandate above are added for you."
                />
                <Button size="xs" variant="ghost" onPress={() => setContext(CONTEXT_TEMPLATE)}>
                  Insert template
                </Button>
              </div>
            </details>

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
            <DecisionResult
              request={current.request}
              result={current.result}
              onStepUp={() => {
                // Ask the same question again once the person has confirmed with their passkey.
                void confirmIdentity().then((ok) => {
                  if (ok) evaluate.mutate(current.request);
                });
              }}
            />
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
