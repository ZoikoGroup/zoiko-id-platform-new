"use client";

import type { Mandate, MandateCreate } from "@zoiko/api-client";
import { Banner, Button, DateTime, Dialog, FieldGroup, Select, Switch, TextField } from "@zoiko/ui";
import { Info } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, listInput, newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  CURRENCY_RE,
  DECIMAL_RE,
  FormProblem,
  GrantPicker,
  PrincipalField,
  RISK_OPTIONS,
  ScopeList,
  UUID_RE,
  defaultWindow,
  intInput,
  localToIso,
  useIdempotency,
  useMe,
} from "../authority/shared";
import { MANDATES_KEY } from "./mandate-status";

export function CreateMandateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (m: Mandate) => void }) {
  const me = useMe();
  const [win] = useState(() => defaultWindow(30));
  const [f, setF] = useState({
    grant: "",
    executor: "",
    purpose: "",
    actions: "",
    resources: "",
    tools: "",
    jurisdictions: "",
    currency: "",
    value: "",
    perTx: "",
    count: "",
    dataBytes: "",
    from: win.from,
    to: win.to,
    delegate: false,
    depth: "1",
    risk: "3",
  });
  const set = <K extends keyof typeof f>(k: K) => (v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const [problem, setProblem] = useState<string | null>(null);
  const keyFor = useIdempotency(newIdempotencyKey);

  const create = useCellMutation((api, v: { body: MandateCreate; key: string }) => api.mandates.create(v.body, v.key), {
    invalidate: [MANDATES_KEY],
    success: "Mandate created; activate it when ready",
    onSuccess: (m) => onCreated(m),
  });

  function build(): MandateCreate | string {
    if (!UUID_RE.test(f.grant)) return "Choose the source grant.";
    if (!UUID_RE.test(f.executor)) return "Choose the executor.";
    if (!UUID_RE.test(f.purpose.trim())) return "The purpose ID must be a UUID.";
    const allowed_actions = listInput(f.actions);
    const resource_scopes = listInput(f.resources);
    if (!allowed_actions.length) return "List at least one action.";
    if (!resource_scopes.length) return "List at least one resource scope.";
    if (allowed_actions.length > 64 || resource_scopes.length > 64) return "At most 64 actions and 64 resource scopes.";
    const from = localToIso(f.from);
    const to = localToIso(f.to);
    if (!from || !to) return "Enter a valid start and end time.";
    if (new Date(to) <= new Date(from)) return "The end of the window must be after its start.";
    const value = f.value.trim();
    const perTx = f.perTx.trim();
    const currency = f.currency.trim();
    if ((value || perTx) && !CURRENCY_RE.test(currency)) return "Value limits need a three-letter currency, e.g. USD.";
    if (value && !DECIMAL_RE.test(value)) return "The value limit must be a non-negative decimal, e.g. 500.00.";
    if (perTx && !DECIMAL_RE.test(perTx)) return "The per-transaction limit must be a non-negative decimal.";
    const count_limit = intInput(f.count);
    const data_limit_bytes = intInput(f.dataBytes);
    if (Number.isNaN(count_limit)) return "The count limit must be a whole number.";
    if (Number.isNaN(data_limit_bytes)) return "The data limit must be a whole number of bytes.";
    const depth = intInput(f.depth) ?? 0;
    if (f.delegate && (Number.isNaN(depth) || depth < 1 || depth > 16)) return "Delegation depth must be between 1 and 16.";
    return {
      authority_grant_id: f.grant,
      executor_principal_id: f.executor,
      purpose_id: f.purpose.trim(),
      allowed_actions,
      resource_scopes,
      tools: listInput(f.tools),
      jurisdictions: listInput(f.jurisdictions),
      value_limit: value ? { amount: value, currency } : null,
      per_transaction_value_limit: perTx ? { amount: perTx, currency } : null,
      count_limit: count_limit ?? null,
      data_limit_bytes: data_limit_bytes ?? null,
      valid_from: from,
      valid_to: to,
      delegation: { allowed: f.delegate, max_depth: f.delegate ? depth : 0 },
      risk_class: Number(f.risk),
    };
  }

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const body = build();
    if (typeof body === "string") {
      setProblem(body);
      return;
    }
    setProblem(null);
    create.mutate({ body, key: keyFor(body) });
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Create mandate"
      description="Hand part of a grant you hold to an executor, with a budget and a window."
      size="xl"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} onPress={() => submit()}>
            Create mandate
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-6" aria-label="Create mandate">
        <Banner tone="info" icon={Info} title="A mandate can only narrow a grant">
          You must be the grantee of the source grant. Actions, resources, limits and window must fit inside it, or the cell refuses with{" "}
          <code>GRANT_SCOPE_WIDENING</code>. New mandates start <strong>pending approval</strong> and must be activated before the executor
          can act.
        </Banner>

        <FieldGroup legend="Source and executor">
          <div className="grid gap-4 sm:grid-cols-2">
            <GrantPicker
              label="Source grant"
              isRequired
              value={f.grant}
              onChange={set("grant")}
              granteeId={me}
              description="Active grants you hold."
            />
            <PrincipalField
              label="Executor"
              isRequired
              value={f.executor}
              onChange={set("executor")}
              preferTypes={["AI_AGENT", "SERVICE", "WORKLOAD"]}
              description="Usually an AI agent, service or workload."
            />
            <TextField
              label="Purpose ID"
              isRequired
              value={f.purpose}
              onChange={set("purpose")}
              mono
              placeholder="UUID"
              description="The registered purpose this mandate serves; it must be within the grant's purposes."
              errorMessage={f.purpose.trim() && !UUID_RE.test(f.purpose.trim()) ? "Enter a UUID." : undefined}
            />
            <Select
              label="Risk class"
              value={f.risk}
              onChange={set("risk")}
              options={RISK_OPTIONS}
              description="The risk class the source grant must resolve at."
            />
          </div>
          {UUID_RE.test(f.grant) ? <GrantBounds grantId={f.grant} onUse={(a, r) => setF((s) => ({ ...s, actions: a.join("\n"), resources: r.join("\n") }))} /> : null}
        </FieldGroup>

        <FieldGroup legend="Scope" description="One per line or comma separated. Empty tools or jurisdictions mean no restriction.">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Allowed actions" isRequired multiline rows={3} mono value={f.actions} onChange={set("actions")} placeholder="purchase.create" />
            <TextField label="Resource scopes" isRequired multiline rows={3} mono value={f.resources} onChange={set("resources")} placeholder="vendor:*" />
            <TextField label="Tools" multiline rows={2} mono value={f.tools} onChange={set("tools")} />
            <TextField label="Jurisdictions" multiline rows={2} mono value={f.jurisdictions} onChange={set("jurisdictions")} placeholder="US" />
          </div>
        </FieldGroup>

        <FieldGroup legend="Budget" description="Amounts are decimal strings. Leave a limit empty for none.">
          <div className="grid gap-4 sm:grid-cols-3">
            <TextField label="Currency" value={f.currency} onChange={(v) => set("currency")(v.toUpperCase())} mono placeholder="USD" />
            <TextField label="Total value limit" value={f.value} onChange={set("value")} inputMode="decimal" mono placeholder="500.00" />
            <TextField label="Per-transaction limit" value={f.perTx} onChange={set("perTx")} inputMode="decimal" mono placeholder="50.00" />
            <TextField label="Count limit" value={f.count} onChange={set("count")} inputMode="numeric" mono description="Number of uses." />
            <TextField label="Data limit (bytes)" value={f.dataBytes} onChange={set("dataBytes")} inputMode="numeric" mono />
          </div>
        </FieldGroup>

        <FieldGroup legend="Window and delegation">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Valid from" type="datetime-local" isRequired value={f.from} onChange={set("from")} />
            <TextField label="Valid until" type="datetime-local" isRequired value={f.to} onChange={set("to")} />
            <div className="space-y-2">
              <Switch isSelected={f.delegate} onChange={set("delegate")}>
                Executor may sub-delegate
              </Switch>
              {f.delegate ? <TextField label="Maximum delegation depth" value={f.depth} onChange={set("depth")} inputMode="numeric" mono description="1–16." /> : null}
            </div>
          </div>
        </FieldGroup>

        <FormProblem>{problem}</FormProblem>
        {create.error ? <ApiErrorView error={create.error} title="The mandate was not created" /> : null}
      </form>
    </Dialog>
  );
}

/** What the chosen grant allows, so the mandate can be kept inside it. */
function GrantBounds({ grantId, onUse }: { grantId: string; onUse: (actions: string[], resources: string[]) => void }) {
  const grant = useCellQuery(["authority", "grants", "one", grantId], (api) => api.authority.grant(grantId));
  if (!grant.data) return null;
  const g = grant.data;
  return (
    <div className="mt-4 rounded-lg border border-border bg-canvas p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-medium text-ink">Grant bounds</p>
        {g.actions && g.resources ? (
          <Button size="xs" variant="secondary" onPress={() => onUse(g.actions ?? [], g.resources ?? [])}>
            Use the grant&apos;s actions and resources
          </Button>
        ) : null}
      </div>
      <dl className="mt-2 grid gap-2 sm:grid-cols-2">
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Actions</dt>
          <dd className="mt-0.5">
            <ScopeList values={g.actions} any="Any action" />
          </dd>
        </div>
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Resources</dt>
          <dd className="mt-0.5">
            <ScopeList values={g.resources} any="Any resource" />
          </dd>
        </div>
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Value limit</dt>
          <dd className="tabular font-mono text-xs">{g.value_limit ? `${g.value_limit.amount} ${g.value_limit.currency}` : "None"}</dd>
        </div>
        <div>
          <dt className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Window ends</dt>
          <dd>
            <DateTime value={g.valid_to} />
          </dd>
        </div>
      </dl>
    </div>
  );
}
