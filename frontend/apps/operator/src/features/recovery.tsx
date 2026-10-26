"use client";

import type { RecoveryKind, RecoveryOperation } from "@zoiko/api-client";
import {
  Banner,
  Button,
  Card,
  Checkbox,
  Code,
  CopyId,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  KeyValues,
  PageHeader,
  RadioCards,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { CheckCircle2, Circle, CircleDot, CircleX, LifeBuoy, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint, KIND_CAPABILITY, PROBES, seconds } from "./recovery-shared";

const OPS_KEY = ["platform", "operations"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CELL = /^[a-z0-9-]{1,40}$/;

const KINDS: { id: RecoveryKind; label: string; description: string }[] = [
  { id: "FAILOVER", label: "Disaster failover", description: "The tenant's cell is lost: promote its approved recovery cell." },
  { id: "MIGRATION", label: "Planned migration", description: "Move a tenant to another permitted cell under a single writer." },
  { id: "FAILBACK", label: "Failback", description: "Return a tenant to its rebuilt original cell after a failover." },
  { id: "RESTORE", label: "Restore", description: "Restore a backup / point in time into an isolated environment." },
];

function Declare({ onClose, onDeclared }: { onClose: () => void; onDeclared: (op: RecoveryOperation) => void }) {
  const [kind, setKind] = useState<RecoveryKind>("FAILOVER");
  const [tenant, setTenant] = useState("");
  const [target, setTarget] = useState("");
  const declare = useCellMutation(
    (api) => (kind === "RESTORE" ? api.platform.restore(target.trim(), tenant.trim() || null) : api.platform.declare(kind, tenant.trim(), target.trim())),
    { invalidate: [OPS_KEY], success: "Operation declared", onSuccess: (op) => onDeclared(op) },
  );
  const tenantOk = kind === "RESTORE" ? !tenant.trim() || UUID.test(tenant.trim()) : UUID.test(tenant.trim());
  const valid = tenantOk && CELL.test(target.trim());
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !declare.isPending && onClose()}
      title="Declare a recovery operation"
      description="The runbook then runs strictly in order. Nothing is skipped and a failed gate blocks rather than risk two writers."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant={kind === "FAILOVER" ? "danger" : "primary"} pending={declare.isPending} isDisabled={!valid} onPress={() => declare.mutate(undefined)}>
            Declare
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RadioCards label="Kind" value={kind} onChange={(v) => setKind(v as RecoveryKind)} columns={2} options={KINDS} />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={kind === "RESTORE" ? "Tenant ID (optional)" : "Tenant ID"}
            isRequired={kind !== "RESTORE"}
            mono
            value={tenant}
            onChange={setTenant}
            errorMessage={tenant && !tenantOk ? "A tenant UUID." : undefined}
          />
          <TextField
            label={kind === "RESTORE" ? "Cell to restore" : "Target cell"}
            isRequired
            mono
            value={target}
            onChange={setTarget}
            placeholder="eu-1-dr"
            errorMessage={target && !CELL.test(target.trim()) ? "Lowercase letters, digits and hyphens." : undefined}
          />
        </div>
        <p className="text-xs text-ink-muted">
          Needs an ACTIVE <span className="font-mono">{KIND_CAPABILITY[kind]}</span> grant for{" "}
          <span className="font-mono">{kind === "RESTORE" && !tenant.trim() ? `cell:${target || "<cell>"}` : `tenant:${tenant || "<tenant>"}`}</span>.
        </p>
        {declare.error ? (
          <>
            <ApiErrorView error={declare.error} title="Not declared" />
            <CapabilityHint error={declare.error} capability={KIND_CAPABILITY[kind] ?? ""} />
          </>
        ) : null}
      </div>
    </Dialog>
  );
}

function StepRunner({ op }: { op: RecoveryOperation }) {
  const step = op.next_step;
  const [evidence, setEvidence] = useState("");
  const [probes, setProbes] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");
  const [aborting, setAborting] = useState(false);
  const key = [...OPS_KEY, op.operation_id];
  const advance = useCellMutation(
    (api) =>
      api.platform.advance(op.operation_id, {
        step: step?.name ?? "",
        evidence_ref: evidence.trim() || null,
        probes: step && PROBES[step.name] ? probes : null,
      }),
    {
      invalidate: [OPS_KEY, key],
      success: (r) => (r.status === "COMPLETED" ? "Operation completed" : `${step?.name ?? "Step"} done`),
      onSuccess: () => {
        setEvidence("");
        setProbes({});
      },
    },
  );
  const abort = useCellMutation((api) => api.platform.abort(op.operation_id, reason.trim()), { invalidate: [OPS_KEY, key], success: "Operation aborted" });
  if (!step || (op.status !== "OPEN" && op.status !== "BLOCKED")) return null;
  const probeList = PROBES[step.name];
  const confirm = step.type === "CONFIRM";
  const ready = !confirm || (evidence.trim().length > 0 && (!probeList || probeList.every((p) => probes[p.id])));
  return (
    <Card title={`Next: ${step.name}`} description={step.detail}>
      <div className="space-y-4">
        {op.status === "BLOCKED" ? (
          <Banner tone="warn" title={`Blocked: ${op.blocked_code ?? ""}`}>
            Fix the cause (readiness, residency, probes), then retry this step, or abort before cutover.
          </Banner>
        ) : null}
        {confirm ? (
          <>
            <p className="text-sm text-ink-muted">
              An infrastructure action done outside the platform. Do it, then record the reference that proves it (change ID, ticket, log link).
            </p>
            <TextField label="Evidence reference" isRequired mono value={evidence} onChange={setEvidence} placeholder="CHG-1234" />
            {probeList ? (
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium text-ink">Every probe must pass</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {probeList.map((p) => (
                    <Checkbox key={p.id} isSelected={!!probes[p.id]} onChange={(v) => setProbes((s) => ({ ...s, [p.id]: v }))}>
                      {p.label}
                    </Checkbox>
                  ))}
                </div>
              </fieldset>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-ink-muted">The platform runs this step itself and records the gate report.</p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button pending={advance.isPending} isDisabled={!ready} onPress={() => advance.mutate(undefined)}>
            {confirm ? "Confirm step" : "Run step"}
          </Button>
          {op.epoch_after === null ? (
            <Button variant="ghost" onPress={() => setAborting((a) => !a)}>
              Abort…
            </Button>
          ) : null}
        </div>
        {advance.error ? (
          <>
            <ApiErrorView error={advance.error} title="Step not completed" />
            <CapabilityHint error={advance.error} capability={KIND_CAPABILITY[op.kind] ?? ""} />
          </>
        ) : null}
        {aborting ? (
          <div className="space-y-2 rounded-card border border-border p-3">
            <TextField label="Why abort" value={reason} onChange={setReason} />
            <Button size="sm" variant="danger" pending={abort.isPending} isDisabled={reason.trim().length < 4} onPress={() => abort.mutate(undefined)}>
              Abort operation
            </Button>
            {abort.error ? <ApiErrorView error={abort.error} /> : null}
          </div>
        ) : null}
        {op.epoch_after !== null ? (
          <p className="text-xs text-ink-muted">The epochs have advanced: the new writer is authoritative. The only way back is a governed failback.</p>
        ) : null}
      </div>
    </Card>
  );
}

function OperationDetail({ id }: { id: string }) {
  const op = useCellQuery([...OPS_KEY, id], (api) => api.platform.operation(id), { refetchInterval: 10_000 });
  return (
    <QueryView query={op}>
      {(o) => (
        <div className="space-y-4">
          <KeyValues
            columns={3}
            items={[
              ["Kind", o.kind.toLowerCase()],
              ["Status", <StatusBadge key="s" state={o.status} kind="Operation" size="xs" />],
              ["Tenant", o.tenant_id ? <CopyId key="t" value={o.tenant_id} label="tenant" /> : "—"],
              ["From", <span key="f" className="font-mono">{o.source_cell_id}</span>],
              ["To", <span key="d" className="font-mono">{o.target_cell_id}</span>],
              ["Epoch", o.epoch_after !== null ? `${o.epoch_before ?? "—"} → ${o.epoch_after}` : (o.epoch_before ?? "—")],
              ["Declared", <DateTime key="a" value={o.declared_at} />],
              ["Completed", o.completed_at ? <DateTime key="c" value={o.completed_at} /> : "—"],
              ["Observed RTO", seconds(o.observed_rto_s)],
            ]}
          />
          <ol className="space-y-2" aria-label="Runbook">
            {o.runbook.map((s, i) => {
              const current = i === o.steps_done && (o.status === "OPEN" || o.status === "BLOCKED");
              const blocked = current && o.status === "BLOCKED";
              const Icon = s.done ? CheckCircle2 : blocked ? CircleX : current ? CircleDot : Circle;
              return (
                <li key={s.name} className="flex items-start gap-3">
                  <Icon aria-hidden className={`mt-0.5 size-4 ${s.done ? "text-ok-fg" : blocked ? "text-danger-fg" : current ? "text-brand-700" : "text-ink-subtle"}`} />
                  <div>
                    <div className="text-sm font-medium text-ink">
                      {i + 1}. {s.name} <span className="text-xs font-normal text-ink-muted">{s.type === "AUTO" ? "platform" : "operator"}</span>
                    </div>
                    <div className="text-xs text-ink-muted">{s.detail}</div>
                  </div>
                </li>
              );
            })}
          </ol>
          <StepRunner op={o} />
          {o.steps?.length ? (
            <Card title="Step log" description="Append-only: who did what, with the evidence reference and gate report.">
              <ul className="divide-y divide-border">
                {o.steps.map((s) => (
                  <li key={s.seq} className="space-y-1 py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink">{s.step}</span>
                      <StatusBadge state={s.outcome} kind="Step" size="xs" />
                      <DateTime value={s.at} />
                      {s.evidence_ref ? <Code>{s.evidence_ref}</Code> : null}
                    </div>
                    {s.report.length ? (
                      <ul className="space-y-0.5 text-xs text-ink-muted">
                        {s.report.map((g) => (
                          <li key={g.gate_id}>
                            {g.status === "PASS" ? "✓" : "✗"} <span className="font-mono">{g.gate_id}</span>
                            {g.detail ? ` — ${g.detail}` : ""}
                            {g.code ? <> (<Code>{g.code}</Code>)</> : null}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>
      )}
    </QueryView>
  );
}

/** Governed recovery operations (RESILIENCE-001 sections 15-20; TENANCY-001 section 15). */
export function RecoveryPage() {
  const ops = useCellQuery(OPS_KEY, (api) => api.platform.operations(), { refetchInterval: 15_000 });
  const [declaring, setDeclaring] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<RecoveryOperation>[] = [
    { key: "kind", header: "Operation", pinned: true, render: (o) => <span className="font-medium text-ink">{o.kind.toLowerCase()}</span> },
    { key: "tenant", header: "Tenant", render: (o) => (o.tenant_id ? <span className="font-mono text-2xs">{o.tenant_id}</span> : "—") },
    { key: "route", header: "Cells", render: (o) => <span className="font-mono text-xs">{o.source_cell_id} → {o.target_cell_id}</span> },
    { key: "status", header: "Status", render: (o) => <StatusBadge state={o.status} kind="Operation" size="xs" /> },
    { key: "step", header: "Progress", render: (o) => `${o.steps_done}/${o.runbook.length}${o.next_step ? ` · next ${o.next_step.name}` : ""}` },
    { key: "at", header: "Declared", render: (o) => <DateTime value={o.declared_at} />, sortValue: (o) => o.declared_at },
    {
      key: "a",
      header: "",
      align: "end",
      render: (o) => (
        <Button size="xs" variant="secondary" onPress={() => setOpen(o.operation_id)}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        icon={LifeBuoy}
        title="Recovery operations"
        description="Failover, migration, failback and restore as enforced runbooks: strictly ordered, gated, evidenced."
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setDeclaring(true)}>
            Declare operation
          </Button>
        }
      />
      <Card padded={false}>
        <QueryView query={ops}>
          {(r) =>
            r.operations.length ? (
              <DataTable caption="Recovery operations" columns={columns} rows={r.operations} rowKey={(o) => o.operation_id} />
            ) : (
              <EmptyState title="No recovery operations" icon={LifeBuoy}>
                Declared operations and their step logs appear here.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {declaring ? (
        <Declare
          onClose={() => setDeclaring(false)}
          onDeclared={(op) => {
            setDeclaring(false);
            setOpen(op.operation_id);
          }}
        />
      ) : null}
      {open ? (
        <Dialog isOpen onOpenChange={(o) => !o && setOpen(null)} title="Recovery operation" size="lg" footer={<Button onPress={() => setOpen(null)}>Close</Button>}>
          <OperationDetail id={open} />
        </Dialog>
      ) : null}
    </>
  );
}
