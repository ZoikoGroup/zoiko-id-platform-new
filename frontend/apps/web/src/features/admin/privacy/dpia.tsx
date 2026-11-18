"use client";

import type { DpiaPackage, DpiaPackageSummary, DpiaRisk } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ClipboardList, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** DPIA workbench (ZID-DTV-WF-04 B03, B11; ZID-PRI-004 sections 18 and 23; CR-108). A package is the impact assessment for one market, product and
 * version: its risk register (likelihood x impact, inherent and residual), the eight release gates each signed by its accountable roles (never by the
 * author, never twice by one person), and the decision. A biometric method reads the package filed as `biometric.face_match` or `biometric.liveness`
 * at the engine version, live, at every session. */

const DPIA_KEY = [...PRIVACY_KEY, "dpia"];

/** The release gates and who signs each (ZID-PRI-004 section 23; mirrors the server's `GATES`). */
export const GATES: Record<string, { name: string; roles: string[] }> = {
  G0: { name: "Scope", roles: ["PRODUCT", "DATA_OWNER"] },
  G1: { name: "Legal privacy", roles: ["LEGAL", "PRIVACY_DPO"] },
  G2: { name: "DPIA", roles: ["CONTROLLER", "PRIVACY_DPO"] },
  G3: { name: "Biometric security", roles: ["SECURITY_CISO"] },
  G4: { name: "Quality and fairness", roles: ["ML_LEAD", "INDEPENDENT_QA"] },
  G5: { name: "Supplier", roles: ["VENDOR_MANAGER", "LEGAL"] },
  G6: { name: "Ecosystem", roles: ["PRODUCT_OWNER"] },
  G7: { name: "Launch", roles: ["CTO", "RELEASE_MANAGER"] },
};

const PRODUCTS = [
  { id: "biometric.face_match", label: "biometric.face_match (face match)" },
  { id: "biometric.liveness", label: "biometric.liveness (liveness)" },
  { id: "OTHER", label: "Another product" },
];
const SCALE = ["1", "2", "3", "4", "5"].map((n) => ({ id: n, label: n }));

/** L x I bands of section 18: 1-5 low, 6-11 moderate, 12-17 high, 18-25 critical. */
export function band(score: number): { label: string; tone: "ok" | "info" | "warn" | "danger" } {
  if (score >= 18) return { label: "Critical", tone: "danger" };
  if (score >= 12) return { label: "High", tone: "warn" };
  if (score >= 6) return { label: "Moderate", tone: "info" };
  return { label: "Low", tone: "ok" };
}

function Score({ l, i }: { l: number; i: number }) {
  const b = band(l * i);
  return (
    <Badge tone={b.tone}>
      {l * i} · {b.label}
    </Badge>
  );
}

function ReleaseBadge({ ready }: { ready: boolean }) {
  return <Badge tone={ready ? "ok" : "warn"}>{ready ? "Release ready" : "Not ready"}</Badge>;
}

/* ----------------------------------------------------------------------------------------------------------------------------- create */

function CreateDialog({ isOpen, onClose, onCreated }: { isOpen: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const blank = { market: "GB", product: "biometric.face_match", other: "", version: "", controller: "", purpose_code: "IDENTITY_VERIFICATION", model_version: "" };
  const [f, setF] = useState(blank);
  const set = (k: keyof typeof blank) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const product = f.product === "OTHER" ? f.other.trim() : f.product;
  const create = useCellMutation(
    (api) =>
      api.privacy.v2.createDpiaPackage({
        market: f.market.trim().toUpperCase(),
        product,
        version: f.version.trim(),
        controller: f.controller.trim(),
        purpose_code: f.purpose_code.trim().toUpperCase(),
        model_version: f.model_version.trim() || null,
      }),
    {
      invalidate: [DPIA_KEY],
      success: "DPIA package opened",
      onSuccess: (p: DpiaPackage) => {
        setF(blank);
        onClose();
        onCreated(p.package_id);
      },
    },
  );
  const ready = /^[A-Za-z]{2}$/.test(f.market.trim()) && product.length >= 2 && f.version.trim() && f.controller.trim().length >= 2 && /^[A-Z][A-Z0-9_]{1,63}$/.test(f.purpose_code.trim().toUpperCase());
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Open a DPIA package"
      description="One package per market, product and version. For a biometric method, the version is the exact engine version it assesses."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!ready} onPress={() => create.mutate(undefined)}>
            Open package
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Market (country)" value={f.market} onChange={set("market")} isRequired />
        <Select label="Product" value={f.product} onChange={set("product")} options={PRODUCTS} />
        {f.product === "OTHER" ? <TextField label="Product name" value={f.other} onChange={set("other")} mono isRequired className="sm:col-span-2" /> : null}
        <TextField label="Version" description="for a biometric method: the engine version" value={f.version} onChange={set("version")} mono isRequired />
        <TextField label="Model version" description="optional: must be the evaluated model in service" value={f.model_version} onChange={set("model_version")} mono />
        <TextField label="Controller" description="the legal entity responsible" value={f.controller} onChange={set("controller")} isRequired />
        <TextField label="Purpose code" value={f.purpose_code} onChange={set("purpose_code")} mono isRequired />
      </div>
      {create.error ? <ApiErrorView error={create.error} /> : null}
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- detail */

function RiskForm({ pkg, onDone }: { pkg: DpiaPackage; onDone: () => void }) {
  const next = `R${String(pkg.risks.length + 1).padStart(2, "0")}`;
  const blank = { risk_id: next, title: "", mitigation: "", owner: "", il: "3", ii: "3", rl: "2", ri: "2" };
  const [f, setF] = useState(blank);
  const [consultation, setConsultation] = useState(pkg.consultation_ref ?? "");
  const set = (k: keyof typeof blank) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const risk: DpiaRisk = {
    risk_id: f.risk_id.trim(),
    title: f.title.trim(),
    mitigation: f.mitigation.trim(),
    owner: f.owner.trim(),
    inherent_likelihood: Number(f.il),
    inherent_impact: Number(f.ii),
    residual_likelihood: Number(f.rl),
    residual_impact: Number(f.ri),
  };
  const residualAboveInherent = risk.residual_likelihood * risk.residual_impact > risk.inherent_likelihood * risk.inherent_impact;
  const keep = pkg.risks.map(({ risk_id, title, mitigation, owner, inherent_likelihood, inherent_impact, residual_likelihood, residual_impact }) => ({
    risk_id,
    title,
    mitigation,
    owner,
    inherent_likelihood,
    inherent_impact,
    residual_likelihood,
    residual_impact,
  }));
  const save = useCellMutation((api) => api.privacy.v2.setDpiaRisks(pkg.package_id, [...keep, risk], consultation.trim() || null), {
    invalidate: [DPIA_KEY],
    success: "Risk added",
    onSuccess: () => {
      setF(blank);
      onDone();
    },
  });
  const ready = /^R[0-9]{2,3}$/.test(risk.risk_id) && risk.title && risk.mitigation && risk.owner && !residualAboveInherent && !pkg.risks.some((r) => r.risk_id === risk.risk_id);
  return (
    <div className="space-y-3 rounded-[var(--radius-control)] border border-border p-4">
      <p className="text-sm font-medium">Add a risk</p>
      <div className="grid gap-3 sm:grid-cols-4">
        <TextField label="Risk ID" value={f.risk_id} onChange={set("risk_id")} mono isRequired />
        <TextField label="Title" value={f.title} onChange={set("title")} isRequired className="sm:col-span-3" />
        <TextField label="Mitigation" value={f.mitigation} onChange={set("mitigation")} multiline rows={2} isRequired className="sm:col-span-3" />
        <TextField label="Owner" value={f.owner} onChange={set("owner")} isRequired />
        <Select label="Inherent likelihood" value={f.il} onChange={set("il")} options={SCALE} />
        <Select label="Inherent impact" value={f.ii} onChange={set("ii")} options={SCALE} />
        <Select label="Residual likelihood" value={f.rl} onChange={set("rl")} options={SCALE} />
        <Select label="Residual impact" value={f.ri} onChange={set("ri")} options={SCALE} />
        <TextField
          label="Consultation reference"
          description="needed when any residual risk is high or critical"
          value={consultation}
          onChange={setConsultation}
          className="sm:col-span-4"
        />
      </div>
      {residualAboveInherent ? <Banner tone="warn">The residual score cannot be higher than the inherent score.</Banner> : null}
      <Button size="sm" icon={<Plus aria-hidden className="size-4" />} pending={save.isPending} isDisabled={!ready} onPress={() => save.mutate(undefined)}>
        Add risk
      </Button>
      {save.error ? <ApiErrorView error={save.error} /> : null}
    </div>
  );
}

function SignGate({ pkg }: { pkg: DpiaPackage }) {
  const [gate, setGate] = useState("G0");
  const [role, setRole] = useState(GATES.G0!.roles[0]!);
  const [evidence, setEvidence] = useState("");
  const roles = GATES[gate]!.roles;
  const chosenRole = roles.includes(role) ? role : roles[0]!;
  const sign = useCellMutation((api) => api.privacy.v2.signDpiaGate(pkg.package_id, { gate, role: chosenRole, evidence_uri: evidence.trim() }), {
    invalidate: [DPIA_KEY],
    success: `${gate} signed as ${chosenRole}`,
    onSuccess: () => setEvidence(""),
  });
  const signed = (g: string, r: string) => Boolean(pkg.gates?.[g]?.[r]);
  return (
    <div className="space-y-3">
      <ul className="grid gap-2 sm:grid-cols-2" aria-label="Release gates">
        {Object.entries(GATES).map(([g, def]) => (
          <li key={g} className="rounded-[var(--radius-control)] border border-border px-3 py-2 text-sm">
            <span className="font-medium">
              {g} · {def.name}
            </span>
            <span className="mt-1 flex flex-wrap gap-1">
              {def.roles.map((r) => (
                <Badge key={r} tone={signed(g, r) ? "ok" : "neutral"}>
                  {r} {signed(g, r) ? "signed" : "pending"}
                </Badge>
              ))}
            </span>
          </li>
        ))}
      </ul>
      {pkg.state === "DRAFT" || pkg.state === "IN_REVIEW" ? (
        <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-control)] border border-border p-4">
          <Select label="Gate" value={gate} onChange={setGate} options={Object.entries(GATES).map(([g, d]) => ({ id: g, label: `${g} · ${d.name}` }))} />
          <Select label="Signing as" value={chosenRole} onChange={setRole} options={roles.map((r) => ({ id: r, label: r }))} />
          <TextField label="Evidence" description="link or reference to what you reviewed" value={evidence} onChange={setEvidence} isRequired />
          <Button size="sm" pending={sign.isPending} isDisabled={evidence.trim().length < 3} onPress={() => sign.mutate(undefined)}>
            Sign gate
          </Button>
        </div>
      ) : null}
      {sign.error ? <ApiErrorView error={sign.error} title="Not signed" /> : null}
    </div>
  );
}

function Decide({ pkg }: { pkg: DpiaPackage }) {
  const [rationale, setRationale] = useState("");
  const [days, setDays] = useState("365");
  const decide = useCellMutation(
    (api, approve: boolean) => api.privacy.v2.decideDpiaPackage(pkg.package_id, { approve, rationale: rationale.trim(), valid_days: Number(days) }),
    { invalidate: [DPIA_KEY], success: "Decision recorded" },
  );
  if (pkg.state !== "DRAFT" && pkg.state !== "IN_REVIEW") return null;
  const ok = rationale.trim().length >= 3 && Number(days) >= 1 && Number(days) <= 1095;
  return (
    <div className="space-y-3 rounded-[var(--radius-control)] border border-border p-4">
      <p className="text-sm font-medium">Decision</p>
      <p className="text-xs text-ink-muted">The author cannot decide. Approval is refused while any release blocker remains.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField label="Rationale" value={rationale} onChange={setRationale} multiline rows={2} isRequired className="sm:col-span-2" />
        <TextField label="Valid for (days)" value={days} onChange={setDays} inputMode="numeric" />
      </div>
      <div className="flex gap-2">
        <Button size="sm" pending={decide.isPending} isDisabled={!ok || !pkg.release.ready} onPress={() => decide.mutate(true)}>
          Approve
        </Button>
        <Button size="sm" variant="danger" pending={decide.isPending} isDisabled={!ok} onPress={() => decide.mutate(false)}>
          Reject
        </Button>
      </div>
      {decide.error ? <ApiErrorView error={decide.error} title="Not decided" /> : null}
    </div>
  );
}

function PackageDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useCellQuery([...DPIA_KEY, "one", id], (api) => api.privacy.v2.dpiaPackage(id!), { enabled: id !== null });
  const [adding, setAdding] = useState(false);
  const riskCols: Column<DpiaRisk>[] = [
    { key: "id", header: "Risk", pinned: true, render: (r) => <span className="font-mono text-xs">{r.risk_id}</span> },
    { key: "title", header: "Title", render: (r) => r.title },
    { key: "owner", header: "Owner", render: (r) => r.owner },
    { key: "inherent", header: "Inherent", render: (r) => <Score l={r.inherent_likelihood} i={r.inherent_impact} /> },
    { key: "residual", header: "Residual", render: (r) => <Score l={r.residual_likelihood} i={r.residual_impact} /> },
    { key: "mitigation", header: "Mitigation", render: (r) => <span className="text-xs">{r.mitigation}</span> },
  ];
  return (
    <Dialog isOpen={id !== null} onOpenChange={(o) => !o && onClose()} title="DPIA package" size="xl">
      <QueryView query={q}>
        {(p) => (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono">
                {p.market} · {p.product} · {p.version}
              </span>
              <StatusBadge state={p.state} kind="DPIA" />
              <ReleaseBadge ready={p.release.ready} />
              {p.approved_until ? (
                <span className="text-ink-muted">
                  valid until <DateTime value={p.approved_until} style="date" />
                </span>
              ) : null}
            </div>
            {p.release.blockers.length ? (
              <Banner tone="warn" title={`${p.release.blockers.length} release blocker(s)`}>
                {p.release.blockers.includes("DPIA_POLICY_CHANGED") ? (
                  <span className="block">
                    The purpose, retention or provider policy changed after this package was approved. Biometric methods resting on it are stopped until
                    a new version is approved against the policy now in force.
                  </span>
                ) : null}
                <span className="mt-1 flex flex-wrap gap-1">
                  {p.release.blockers.map((b) => (
                    <Badge key={b} tone="warn">
                      {b}
                    </Badge>
                  ))}
                </span>
              </Banner>
            ) : null}
            <section aria-label="Risk register" className="space-y-3">
              <h3 className="text-sm font-semibold">Risk register</h3>
              <DataTable caption="Risk register" columns={riskCols} rows={p.risks} rowKey={(r) => r.risk_id} empty={<EmptyState icon={ClipboardList} title="No risks assessed yet" />} />
              {p.state === "DRAFT" || p.state === "IN_REVIEW" ? (
                adding ? (
                  <RiskForm pkg={p} onDone={() => setAdding(false)} />
                ) : (
                  <Button size="sm" variant="secondary" icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
                    Add a risk
                  </Button>
                )
              ) : null}
            </section>
            <section aria-label="Release gates" className="space-y-3">
              <h3 className="text-sm font-semibold">Release gates</h3>
              <SignGate pkg={p} />
            </section>
            <Decide pkg={p} />
          </div>
        )}
      </QueryView>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- list */

export function DpiaSection() {
  const q = useCellQuery(DPIA_KEY, (api) => api.privacy.v2.dpiaPackages());
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const cols: Column<DpiaPackageSummary>[] = [
    { key: "what", header: "Market · product · version", pinned: true, render: (r) => <span className="font-mono text-xs">{`${r.market} · ${r.product} · ${r.version}`}</span> },
    { key: "state", header: "State", render: (r) => <StatusBadge state={r.state} kind="DPIA" /> },
    { key: "release", header: "Release", render: (r) => <ReleaseBadge ready={r.release.ready} /> },
    { key: "blockers", header: "Blockers", render: (r) => (r.release.blockers.length ? `${r.release.blockers.length}` : "None") },
    { key: "risks", header: "Risks", render: (r) => r.risk_count },
    { key: "gates", header: "Signatures", render: (r) => `${r.gates_signed.length} / 14` },
    { key: "until", header: "Valid until", render: (r) => (r.approved_until ? <DateTime value={r.approved_until} style="date" /> : "—") },
    {
      key: "open",
      header: "",
      align: "end",
      render: (r) => (
        <Button size="xs" variant="secondary" onPress={() => setOpen(r.package_id)} aria-label={`Open ${r.market} ${r.product} ${r.version}`}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <div className="space-y-5">
      <Card
        padded={false}
        title="DPIA packages"
        description="Impact assessments with their risk register and release gates. A biometric method in a country runs only while its package is approved, in date and fully signed."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Open package
          </Button>
        }
      >
        <QueryView query={q}>
          {(d) => (
            <DataTable
              caption="DPIA packages"
              columns={cols}
              rows={d.items}
              rowKey={(r) => r.package_id}
              empty={<EmptyState icon={ClipboardList} title="No DPIA packages">Biometric methods stay off until one is approved.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      <CreateDialog isOpen={creating} onClose={() => setCreating(false)} onCreated={setOpen} />
      <PackageDialog id={open} onClose={() => setOpen(null)} />
    </div>
  );
}
