"use client";

import type { CatalogueService, ChangeClass, PrrGate, SloState } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, KeyValues, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Activity, ClipboardList, Lock, Snowflake } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint } from "./recovery-shared";

const SERVICES_KEY = ["ops", "services"];
const CLASSES: { id: ChangeClass; label: string }[] = [
  { id: "STANDARD", label: "Standard change" },
  { id: "RISKY", label: "Risky change (schema, auth, crypto, topology)" },
  { id: "FIX", label: "Reliability fix" },
  { id: "SECURITY", label: "Security fix" },
];
/** SRE-001 section 24 production readiness gates. */
const PRR_GATES = [
  ["ownership", "Ownership: owner, on-call, escalation named"],
  ["architecture", "Architecture and threat model approved"],
  ["slos", "SLIs, SLOs, burn alerts and dashboards live"],
  ["telemetry", "Telemetry validated; PII/secret audit passed"],
  ["runbooks", "Runbooks for top failure modes exercised"],
  ["capacity", "2x load gate and scaling limits understood"],
  ["ha_dr", "Multi-AZ, backup/restore and failover drill passed"],
  ["security", "Vulnerability, key, access and supply-chain gates"],
  ["data", "Migration/rollback, retention, deletion, residency"],
  ["evidence", "Required E1-E3 receipts and audit coverage"],
  ["dependencies", "Third-party quotas, fallbacks, escalations tested"],
  ["support", "Support diagnostics and status procedure ready"],
  ["open_risks", "Accepted risks have owner, expiry, controls"],
] as const;

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(v >= 0.999 ? 3 : 1)}%`;
}

function Slos({ slos }: { slos: SloState[] }) {
  return (
    <div className="space-y-3">
      {slos.map((s) => (
        <div key={s.slo_id} className="rounded-card border border-border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-ink">{s.slo_id}</span>
            <span className="text-xs text-ink-muted">target {pct(s.target)}</span>
            {s.budget_state ? <StatusBadge state={s.budget_state} kind="Error budget" size="xs" /> : <StatusBadge state="UNKNOWN" size="xs" />}
            {s.alert && s.alert !== "NONE" ? <Badge tone={s.alert === "PAGE" ? "danger" : "warn"}>{s.alert}</Badge> : null}
          </div>
          {s.windows ? (
            <div className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
              {s.windows.map((w) => (
                <div key={w.window} className={w.firing ? "rounded bg-danger-bg p-2 text-danger-fg" : "rounded bg-sunken p-2 text-ink-muted"}>
                  <div className="font-medium">{w.window}</div>
                  <div>
                    burn {w.long_burn.toFixed(1)}× / {w.short_burn.toFixed(1)}× (page at {w.factor}×)
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          <div className="mt-2 text-xs text-ink-muted">
            {s.events ?? 0} events · good {pct(s.good_ratio)} · budget left {s.budget_remaining === undefined ? "—" : `${Math.round(s.budget_remaining * 100)}%`}
          </div>
        </div>
      ))}
      <p className="text-xs text-ink-muted">This process&apos;s own windows; fleet-wide alerting runs the same windows in Prometheus.</p>
    </div>
  );
}

function ReviewDialog({ service, onClose }: { service: CatalogueService; onClose: () => void }) {
  const [release, setRelease] = useState("");
  const [gates, setGates] = useState<Record<string, PrrGate>>(Object.fromEntries(PRR_GATES.map(([g]) => [g, { gate: g, status: "PASS", evidence_ref: "" }])));
  const set = (g: string, patch: Partial<PrrGate>) => setGates((s) => ({ ...s, [g]: { ...s[g]!, ...patch } }));
  const record = useCellMutation(
    (api) =>
      api.ops.recordReview({
        service_id: service.service_id,
        release_ref: release.trim(),
        gates: Object.values(gates).map((g) => ({
          gate: g.gate,
          status: g.status,
          evidence_ref: g.evidence_ref || null,
          risk_owner: g.status === "ACCEPTED" ? g.risk_owner || null : null,
          risk_expires: g.status === "ACCEPTED" ? g.risk_expires || null : null,
          compensating_control: g.status === "ACCEPTED" ? g.compensating_control || null : null,
        })),
      }),
    { invalidate: [["ops", "readiness", service.service_id]], success: (r) => (r.production_ready ? "Production-ready" : `${r.blocking.length} blocking gate(s)`) },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !record.isPending && onClose()}
      title={`Readiness review: ${service.name}`}
      description="Ready only when every gate passes, or carries an accepted risk with an owner, expiry and compensating control."
      size="lg"
      footer={
        record.data ? (
          <Button onPress={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" onPress={onClose}>
              Cancel
            </Button>
            <Button pending={record.isPending} isDisabled={!release.trim()} onPress={() => record.mutate(undefined)}>
              Record review
            </Button>
          </>
        )
      }
    >
      {record.data ? (
        record.data.production_ready ? (
          <Banner tone="ok" title="Production-ready">Every gate passed or carries a valid accepted risk.</Banner>
        ) : (
          <Banner tone="warn" title="Not production-ready">
            Blocking: {record.data.blocking.map((b) => `${b.gate} (${b.reason.toLowerCase()})`).join(", ")}
          </Banner>
        )
      ) : (
        <div className="space-y-4">
          <TextField label="Release" isRequired mono value={release} onChange={setRelease} placeholder="2026.10.22-1" />
          <ul className="space-y-3">
            {PRR_GATES.map(([id, label]) => {
              const g = gates[id]!;
              return (
                <li key={id} className="space-y-2 rounded-card border border-border p-3">
                  <div className="grid gap-2 sm:grid-cols-[1fr_9rem]">
                    <div className="text-sm text-ink">{label}</div>
                    <Select
                      label="Result"
                      value={g.status}
                      onChange={(v) => set(id, { status: v as PrrGate["status"] })}
                      options={[
                        { id: "PASS", label: "Pass" },
                        { id: "FAIL", label: "Fail" },
                        { id: "ACCEPTED", label: "Risk accepted" },
                      ]}
                    />
                  </div>
                  {g.status === "PASS" ? <TextField label="Evidence reference" mono value={g.evidence_ref ?? ""} onChange={(v) => set(id, { evidence_ref: v })} /> : null}
                  {g.status === "ACCEPTED" ? (
                    <div className="grid gap-2 sm:grid-cols-3">
                      <TextField label="Risk owner (principal ID)" mono value={g.risk_owner ?? ""} onChange={(v) => set(id, { risk_owner: v })} />
                      <TextField label="Expires" type="date" value={g.risk_expires ?? ""} onChange={(v) => set(id, { risk_expires: v })} />
                      <TextField label="Compensating control" value={g.compensating_control ?? ""} onChange={(v) => set(id, { compensating_control: v })} />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {record.error ? <ApiErrorView error={record.error} title="The review was not recorded" /> : null}
        </div>
      )}
    </Dialog>
  );
}

function ServiceDetail({ service }: { service: CatalogueService }) {
  const id = service.service_id;
  const [cls, setCls] = useState<ChangeClass>("STANDARD");
  const [reviewing, setReviewing] = useState(false);
  const health = useCellQuery(["ops", "health", id], (api) => api.ops.health(id), { refetchInterval: 30_000 });
  const policy = useCellQuery(["ops", "policy", id, cls], (api) => api.ops.changePolicy(id, cls));
  const readiness = useCellQuery(["ops", "readiness", id], (api) => api.ops.readiness(id));
  const refresh = [["ops", "health", id], ["ops", "policy", id]];
  const freeze = useCellMutation((api) => api.ops.freeze(id, "OPERATOR_FREEZE"), { invalidate: refresh, success: "Changes frozen" });
  const unfreeze = useCellMutation((api) => api.ops.unfreeze(id), { invalidate: refresh, success: "Freeze lifted" });
  return (
    <div className="space-y-4">
      <KeyValues
        columns={3}
        items={[
          ["Tier", service.tier],
          ["Owner", <span key="o" className="font-mono text-xs">{service.owner}</span>],
          ["On-call", service.oncall],
          ["Dashboard", service.dashboard],
          ["Runbooks", service.runbooks.join(", ")],
          ["Journeys", service.journeys.map((j) => j.prefix).join(", ")],
        ]}
      />
      <QueryView query={health}>
        {(h) => (
          <Card
            title="Health"
            actions={
              h.frozen ? (
                <Button size="xs" variant="secondary" pending={unfreeze.isPending} onPress={() => unfreeze.mutate(undefined)}>
                  Lift freeze
                </Button>
              ) : (
                <Button size="xs" variant="danger" icon={<Snowflake aria-hidden className="size-3.5" />} pending={freeze.isPending} onPress={() => freeze.mutate(undefined)}>
                  Freeze changes
                </Button>
              )
            }
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge state={h.state} kind="Operational state" />
                {h.frozen ? <Badge tone="warn" icon={Lock}>Change freeze</Badge> : null}
                {h.open_incidents.length ? <Badge tone="danger">{h.open_incidents.length} open incident(s)</Badge> : null}
              </div>
              <Slos slos={h.slos} />
              {freeze.error ? <ApiErrorView error={freeze.error} /> : null}
              {unfreeze.error ? (
                <>
                  <ApiErrorView error={unfreeze.error} />
                  <CapabilityHint error={unfreeze.error} capability="platform.change.manage" />
                </>
              ) : null}
            </div>
          </Card>
        )}
      </QueryView>
      <Card title="Change policy" description="What the error budget and any freeze allow right now. Fixes and security changes are never blocked by a budget.">
        <div className="space-y-3">
          <Select label="Change" value={cls} onChange={(v) => setCls(v as ChangeClass)} options={CLASSES} className="max-w-md" />
          <QueryView query={policy}>
            {(p) => (
              <Banner tone={p.allowed ? (p.requires ? "warn" : "ok") : "danger"} title={p.allowed ? (p.requires ? `Allowed with ${p.requires.replace(/_/g, " ")}` : "Allowed") : "Blocked"}>
                Budget {p.budget_state.toLowerCase().replace(/_/g, " ")}
                {p.frozen ? " · freeze in force" : ""} · {p.reason.replace(/_/g, " ").toLowerCase()}
              </Banner>
            )}
          </QueryView>
        </div>
      </Card>
      <Card
        title="Production readiness"
        actions={
          <Button size="xs" variant="secondary" icon={<ClipboardList aria-hidden className="size-3.5" />} onPress={() => setReviewing(true)}>
            Record review
          </Button>
        }
      >
        <QueryView query={readiness}>
          {(r) =>
            r.production_ready ? (
              <Banner tone="ok" title="Production-ready">
                Latest review {r.release_ref ? `for ${r.release_ref}` : ""}: every gate passes.
              </Banner>
            ) : (
              <Banner tone="warn" title="Not production-ready">
                {r.blocking.map((b) => `${b.gate} (${b.reason.toLowerCase().replace(/_/g, " ")})`).join(", ")}
              </Banner>
            )
          }
        </QueryView>
      </Card>
      {reviewing ? <ReviewDialog service={service} onClose={() => setReviewing(false)} /> : null}
    </div>
  );
}

/** Production services: SLOs, error budgets, change policy and readiness (SRE-001 sections 1, 5, 23-24, 29). */
export function ServicesPage() {
  const services = useCellQuery(SERVICES_KEY, (api) => api.ops.services());
  const [open, setOpen] = useState<CatalogueService | null>(null);
  const columns: Column<CatalogueService>[] = [
    { key: "name", header: "Service", pinned: true, render: (s) => <span className="font-medium text-ink">{s.name}</span>, sortValue: (s) => s.name },
    { key: "tier", header: "Tier", render: (s) => <Badge tone={s.tier === "T0" ? "danger" : s.tier === "T1" ? "warn" : "neutral"}>{s.tier}</Badge>, sortValue: (s) => s.tier },
    { key: "owner", header: "Owner", render: (s) => <span className="font-mono text-xs">{s.owner}</span> },
    { key: "slos", header: "SLOs", render: (s) => s.slos.map((x) => `${x.sli} ${pct(x.target)}`).join(" · ") },
    {
      key: "a",
      header: "",
      align: "end",
      render: (s) => (
        <Button size="xs" variant="secondary" onPress={() => setOpen(s)}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader icon={Activity} title="Services" description="Every production service with its owner, tier, SLOs, change policy and readiness." />
      <Card padded={false}>
        <QueryView query={services}>
          {(r) => <DataTable caption="Production services" columns={columns} rows={r.services} rowKey={(s) => s.service_id} />}
        </QueryView>
      </Card>
      {open ? (
        <Dialog isOpen onOpenChange={(o) => !o && setOpen(null)} title={open.name} size="lg" footer={<Button onPress={() => setOpen(null)}>Close</Button>}>
          <ServiceDetail service={open} />
        </Dialog>
      ) : null}
    </>
  );
}
