"use client";

import type { CountryApprovalKey, CountryFeatureKey, CountryFeatureState, CountryOpeningRow, CountryOpeningsView } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, FieldGroup, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { Globe2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { REG, RegError } from "./common";

const MIN_REFERENCE = 3; // the cell's own rule; checked here so the form says what is missing before anything is sent
const today = () => new Date().toISOString().slice(0, 10);
const dayStart = (d: string) => new Date(`${d}T00:00:00Z`).toISOString();

const STATE: Record<CountryFeatureState["in_force"], { label: string; tone: "ok" | "warn" | "neutral" }> = {
  ENABLED: { label: "Open", tone: "ok" },
  SANDBOX: { label: "Test only", tone: "warn" },
  DISABLED: { label: "Closed", tone: "neutral" },
};

type Feature = CountryOpeningsView["features"][number];
type Target = { row: CountryOpeningRow; feature: Feature; state: CountryFeatureState };

export function CountryOpeningsPage() {
  const q = useCellQuery(REG.countryOpenings, (api) => api.regulatory.countryOpenings());
  const [enabling, setEnabling] = useState<Target | null>(null);
  const [disabling, setDisabling] = useState<Target | null>(null);
  return (
    <>
      <PageHeader
        title="Country features"
        description="Choose what people can do in each country. Sign-up, identity checks, organization claims, credentials and enterprise use are separate switches: each one opens when you record its six approvals and the date it starts. Every decision is kept and audited; disabling a feature is a new record, never a deletion."
      />
      <QueryView query={q}>
        {(d) => {
          const total = d.countries.length * d.features.length;
          const open = d.countries.reduce((n, c) => n + (c.features ?? []).filter((f) => f.in_force === "ENABLED").length, 0);
          const deployed = d.environment !== "local" && d.environment !== "test";
          return (
            <div className="space-y-5">
              <Banner tone="info" title="What the six approvals are">
                Each approval stands for a decision an owner made elsewhere (a ticket, a signed document, a minute). Dragging an approval into Confirmed records that you have seen it; this screen cannot make the decisions, so confirm only approvals that really exist.
                {deployed ? " In this environment a test-only setting in the registry file never opens a feature: only a complete record here does." : ""} Under enforce, each feature also needs a policy pack, a market release, a provider entitlement and a privacy overlay: see Enforce readiness.
              </Banner>
              <Card padded={false} title={`${open} of ${total} features open. ${total - open} still need their six approvals.`}>
                <DataTable
                  caption="Country features"
                  columns={columns(d, setEnabling, setDisabling)}
                  rows={d.countries}
                  rowKey={(r) => r.country}
                  empty={
                    <EmptyState icon={Globe2} title="No countries listed">
                      The country registry file lists no country.
                    </EmptyState>
                  }
                />
              </Card>
              {enabling ? <EnableDialog view={d} target={enabling} onClose={() => setEnabling(null)} /> : null}
              {disabling ? <DisableDialog target={disabling} onClose={() => setDisabling(null)} /> : null}
            </div>
          );
        }}
      </QueryView>
    </>
  );
}

function columns(d: CountryOpeningsView, enable: (t: Target) => void, disable: (t: Target) => void): Column<CountryOpeningRow>[] {
  const featureColumns: Column<CountryOpeningRow>[] = d.features.map((feature) => ({
    key: feature.capability,
    header: feature.label,
    render: (row) => {
      const state = (row.features ?? []).find((f) => f.capability === feature.capability);
      if (!state) return <span className="text-ink-subtle">-</span>;
      return <FeatureCell feature={feature} state={state} country={row.name} onEnable={() => enable({ row, feature, state })} onDisable={() => disable({ row, feature, state })} />;
    },
  }));
  return [
    {
      key: "country",
      header: "Country",
      pinned: true,
      render: (r) => (
        <span>
          <span className="font-mono">{r.country}</span> <span className="text-ink-muted">{r.name}</span>
        </span>
      ),
      sortValue: (r) => r.country,
    },
    ...featureColumns,
    {
      key: "count",
      header: "Open",
      render: (r) => `${(r.features ?? []).filter((f) => f.in_force === "ENABLED").length} of ${(r.features ?? []).length}`,
      sortValue: (r) => (r.features ?? []).filter((f) => f.in_force === "ENABLED").length,
    },
  ];
}

/** One switch: what is in force, and the one button that changes it. */
function FeatureCell({ feature, state, country, onEnable, onDisable }: { feature: Feature; state: CountryFeatureState; country: string; onEnable: () => void; onDisable: () => void }) {
  const on = state.in_force === "ENABLED";
  const recorded = state.opening?.status === "OPEN" ? state.opening : null;
  return (
    <div className="space-y-1" title={feature.unlocks}>
      <Badge tone={STATE[state.in_force].tone}>{STATE[state.in_force].label}</Badge>
      {recorded?.effective_from ? (
        <div className="text-2xs text-ink-muted">
          from <DateTime value={recorded.effective_from} />
        </div>
      ) : null}
      <div className="flex gap-1">
        {on ? (
          <Button size="xs" variant="danger-outline" onPress={onDisable} aria-label={`Disable ${feature.label} for ${country}`}>
            Disable
          </Button>
        ) : (
          <Button size="xs" variant="primary" onPress={onEnable} aria-label={`Enable ${feature.label} for ${country}`}>
            Enable
          </Button>
        )}
        {recorded ? (
          <Button size="xs" variant="ghost" onPress={onEnable} aria-label={`Edit ${feature.label} for ${country}`}>
            Edit
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** One approval on the board. `confirmed` is the operator's own act: they drag it across (or press its button) to say the owner's decision is recorded.
 * The reference is optional here; when none is typed the record says it was confirmed in the console, so the cell still has something to keep. */
type Form = { cell: string; date: string; reason: string; confirmed: Set<CountryApprovalKey>; refs: Partial<Record<CountryApprovalKey, string>> };

const DRAG_TYPE = "text/plain";
const defaultReference = (date: string) => `Confirmed in the Operator Console on ${date}`;

function ApprovalCard({
  approval,
  confirmed,
  reference,
  onMove,
  onReference,
}: {
  approval: CountryOpeningsView["approvals"][number];
  confirmed: boolean;
  reference: string;
  onMove: () => void;
  onReference: (v: string) => void;
}) {
  return (
    <li
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, approval.key);
        e.dataTransfer.effectAllowed = "move";
      }}
      className="cursor-grab space-y-2 rounded-[var(--radius-control)] border border-border-strong bg-surface p-3 shadow-sm active:cursor-grabbing"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-ink">{approval.label}</div>
          <div className="text-xs text-ink-muted">Owner: {approval.owner}</div>
        </div>
        <Button size="xs" variant={confirmed ? "ghost" : "secondary"} onPress={onMove} aria-label={`${confirmed ? "Move back" : "Confirm"} ${approval.label}`}>
          {confirmed ? "Undo" : "Confirm"}
        </Button>
      </div>
      {confirmed ? (
        <TextField
          label="Reference"
          description="optional: a ticket or document number"
          value={reference}
          onChange={onReference}
          errorMessage={reference !== "" && reference.trim().length < MIN_REFERENCE ? `At least ${MIN_REFERENCE} characters, or leave blank` : undefined}
        />
      ) : null}
    </li>
  );
}

function Column2({ title, hint, onDropKey, children }: { title: string; hint: string; onDropKey: (key: string) => void; children: ReactNode }) {
  const [over, setOver] = useState(false);
  return (
    <section
      aria-label={title}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        onDropKey(e.dataTransfer.getData(DRAG_TYPE));
      }}
      className={`min-h-40 rounded-[var(--radius-control)] border-2 border-dashed p-3 ${over ? "border-brand-500 bg-brand-50" : "border-border"}`}
    >
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mb-2 text-xs text-ink-muted">{hint}</p>
      <ul className="space-y-2">{children}</ul>
    </section>
  );
}

function EnableDialog({ view, target, onClose }: { view: CountryOpeningsView; target: Target; onClose: () => void }) {
  const { row, feature } = target;
  const needsCell = feature.capability === "REGISTRATION"; // only sign-up decides which cell holds an account
  const current = target.state.opening?.status === "OPEN" ? target.state.opening : null;
  const [f, setF] = useState<Form>(() => ({
    cell: current?.home_cells[0] ?? (view.cells.find((c) => c.country === row.country) ?? view.cells[0])?.cell_id ?? "",
    date: current?.effective_from?.slice(0, 10) ?? today(),
    reason: "",
    confirmed: new Set(Object.keys(current?.approvals ?? {}) as CountryApprovalKey[]),
    refs: { ...(current?.approvals ?? {}) },
  }));
  const save = useCellMutation(
    (api, body: Parameters<typeof api.regulatory.recordCountryOpening>[1]) => api.regulatory.recordCountryOpening(row.country, body),
    { invalidate: [REG.countryOpenings], success: `${feature.label} is enabled for ${row.name}`, onSuccess: onClose },
  );
  const keys = new Set<string>(view.approvals.map((a) => a.key));
  const move = (key: string, confirm: boolean) => {
    if (!keys.has(key)) return; // something else was dropped here
    setF((x) => {
      const next = new Set(x.confirmed);
      if (confirm) next.add(key as CountryApprovalKey);
      else next.delete(key as CountryApprovalKey);
      return { ...x, confirmed: next };
    });
  };
  const refBad = (k: CountryApprovalKey) => (f.refs[k] ?? "") !== "" && (f.refs[k] ?? "").trim().length < MIN_REFERENCE;
  const allConfirmed = view.approvals.every((a) => f.confirmed.has(a.key));
  const ready = (!needsCell || f.cell !== "") && f.date !== "" && allConfirmed && !view.approvals.some((a) => refBad(a.key));
  const card = (a: CountryOpeningsView["approvals"][number], confirmed: boolean) => (
    <ApprovalCard
      key={a.key}
      approval={a}
      confirmed={confirmed}
      reference={f.refs[a.key] ?? ""}
      onMove={() => move(a.key, !confirmed)}
      onReference={(v) => setF((x) => ({ ...x, refs: { ...x.refs, [a.key]: v } }))}
    />
  );
  const submit = () =>
    save.mutate({
      capability: feature.capability as CountryFeatureKey,
      action: "OPEN",
      effective_from: dayStart(f.date),
      home_cells: needsCell ? [f.cell] : [],
      approvals: Object.fromEntries(view.approvals.map((a) => [a.key, (f.refs[a.key] ?? "").trim() || defaultReference(today())])),
      reason: f.reason.trim() || undefined,
    });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={`Enable ${feature.label}: ${row.name}`}
      description={`${feature.unlocks} in this country from the date below. Drag each approval into Confirmed once its owner's decision is on record.`}
      size="xl"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={submit} pending={save.isPending} isDisabled={!ready}>
            {current ? "Save changes" : `Enable ${feature.label}`}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {save.error ? <RegError error={save.error} /> : null}
        <FieldGroup legend={needsCell ? "Where and when" : "When"}>
          {needsCell ? (
            <Select
              label="Home cell"
              description="the cell that holds accounts created in this country"
              options={view.cells.map((c) => ({ id: c.cell_id, label: `${c.cell_id}${c.region ? `  (${c.region}${c.country ? `, ${c.country}` : ""})` : ""}` }))}
              value={f.cell}
              onChange={(v) => setF((x) => ({ ...x, cell: v }))}
              isRequired
            />
          ) : null}
          <TextField label="Takes effect on" description={`${feature.label} stays closed until this day`} type="date" value={f.date} onChange={(v) => setF((x) => ({ ...x, date: v }))} isRequired />
        </FieldGroup>
        <FieldGroup legend="The six approvals">
          <p className="text-xs text-ink-muted">
            {f.confirmed.size} of {view.approvals.length} confirmed. Dragging is your confirmation that the owner&apos;s decision is recorded; each card also has a button, so the board works without a mouse. Add a reference to a card if you have a ticket or document number.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Column2 title="To confirm" hint="drop here to take one back" onDropKey={(k) => move(k, false)}>
              {view.approvals.filter((a) => !f.confirmed.has(a.key)).map((a) => card(a, false))}
            </Column2>
            <Column2 title="Confirmed" hint="drag the approvals here" onDropKey={(k) => move(k, true)}>
              {view.approvals.filter((a) => f.confirmed.has(a.key)).map((a) => card(a, true))}
            </Column2>
          </div>
        </FieldGroup>
        <TextField label="Note" description="optional; kept with the record" value={f.reason} onChange={(v) => setF((x) => ({ ...x, reason: v }))} />
      </div>
    </Dialog>
  );
}

function DisableDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const { row, feature } = target;
  const [reason, setReason] = useState("");
  const save = useCellMutation((api, body: Parameters<typeof api.regulatory.recordCountryOpening>[1]) => api.regulatory.recordCountryOpening(row.country, body), {
    invalidate: [REG.countryOpenings],
    success: `${feature.label} is disabled for ${row.name}`,
    onSuccess: onClose,
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={`Disable ${feature.label}: ${row.name}`}
      description={`New requests for this stop at once. Anything already created is not changed. The decision is recorded; enabling it again needs the six approvals again.`}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onPress={() => save.mutate({ capability: feature.capability as CountryFeatureKey, action: "CLOSE", reason: reason.trim() || undefined })} pending={save.isPending}>
            Disable {feature.label}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {save.error ? <RegError error={save.error} /> : null}
        <TextField label="Why" description="optional; kept with the record" value={reason} onChange={setReason} />
      </div>
    </Dialog>
  );
}
