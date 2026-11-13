"use client";

import type { GateRole, MarketRelease, ReleaseApproverRole, SupplierScope } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Rocket } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Facts, Hash, Mono, REG, Reasons, RegError, upper, Verdict } from "./common";

const GATE_ROLES: GateRole[] = ["legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt"];
const APPROVERS: ReleaseApproverRole[] = ["legal", "security_grc", "qa", "product_owner"];

function OpenReleaseDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [f, setF] = useState({ release_id: "", country: "", product: "INDIVIDUAL_VERIFICATION", purpose: "ENGAGEMENT" });
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const open = useCellMutation(
    (api) => api.regulatory.openRelease({ release_id: f.release_id.trim(), country: upper(f.country), product: upper(f.product), purpose: upper(f.purpose) }),
    { invalidate: [REG.releases], success: (r) => `Release ${r.release_id} opened as a draft`, onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Open a market release"
      description="A market opens only through a release: seven evidence gates, a freeze that pins the packs and suppliers, four approvals of the exact hash, then launch."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => open.mutate(undefined)} pending={open.isPending} isDisabled={!f.release_id || f.country.trim().length !== 2}>
            Open release
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Release ID" description="lowercase, e.g. gb-idv-2026" value={f.release_id} onChange={set("release_id")} mono isRequired />
        <TextField label="Country" value={f.country} onChange={set("country")} isRequired />
        <TextField label="Product" value={f.product} onChange={set("product")} mono isRequired />
        <TextField label="Purpose" value={f.purpose} onChange={set("purpose")} mono isRequired />
      </div>
      {open.error ? <RegError error={open.error} /> : null}
    </Dialog>
  );
}

function scopeFromText(line: string): SupplierScope | null {
  const [entity, scheme, country, service_code] = line.split("/").map((s) => s.trim());
  return entity && scheme && country && service_code ? { entity, scheme, country: country.toUpperCase(), service_code } : null;
}

function ReleaseDialog({ release, onClose }: { release: MarketRelease | null; onClose: () => void }) {
  const [gate, setGate] = useState("RG-01");
  const [gateRole, setGateRole] = useState<GateRole>("legal");
  const [gateStatus, setGateStatus] = useState<"PASSED" | "FAILED">("PASSED");
  const [evidence, setEvidence] = useState("");
  const [suppliers, setSuppliers] = useState("");
  const [approver, setApprover] = useState<ReleaseApproverRole>("legal");
  const opts = { invalidate: [REG.releases, REG.readiness] };
  const r = release;
  const attest = useCellMutation(
    (api, x: MarketRelease) => api.regulatory.attestGate(x.release_id, x.version, { gate, role: gateRole, status: gateStatus, evidence_ref: evidence.trim() }),
    { ...opts, success: "Gate result recorded" },
  );
  const freeze = useCellMutation(
    (api, x: MarketRelease) =>
      api.regulatory.freezeRelease(
        x.release_id,
        x.version,
        suppliers
          .split("\n")
          .map(scopeFromText)
          .filter((s): s is SupplierScope => s !== null),
      ),
    { ...opts, success: "Release frozen: packs and suppliers pinned" },
  );
  const approve = useCellMutation((api, x: MarketRelease) => api.regulatory.approveRelease(x.release_id, x.version, approver, x.release_hash ?? ""), {
    ...opts,
    success: "Approval recorded against the exact hash",
  });
  const launch = useCellMutation((api, x: MarketRelease) => api.regulatory.launchRelease(x.release_id, x.version), { ...opts, success: "Market launched", onSuccess: onClose });
  const withdraw = useCellMutation((api, x: MarketRelease) => api.regulatory.withdrawRelease(x.release_id, x.version), {
    ...opts,
    success: "Market withdrawn: it is closed now",
    onSuccess: onClose,
  });
  const error = attest.error ?? freeze.error ?? approve.error ?? launch.error ?? withdraw.error;
  return (
    <Dialog isOpen={r !== null} onOpenChange={(o) => !o && onClose()} title={r ? `${r.release_id} v${r.version}` : ""} size="xl">
      {r ? (
        <div className="space-y-5">
          <Facts
            items={[
              ["Status", <StatusBadge key="s" state={r.status} kind="Release" />],
              ["Market", `${r.market.country} · ${r.market.product} · ${r.market.purpose}`],
              ["Author", <Mono key="a">{r.author}</Mono>],
              ["Hash to sign", <Hash key="h" value={r.release_hash} />],
              ["Pinned packs", r.pack_refs.map((p) => `${p.pack_id} v${p.version}`).join(", ") || "none"],
              ["Pinned suppliers", r.supplier_scopes.map((s) => `${s.entity}/${s.scheme}/${s.country}/${s.service_code}`).join(", ") || "none"],
              ["Blockers", <Reasons key="b" reasons={r.blockers} />],
              ["Approvals still needed", r.missing_approvals.join(", ") || "none"],
            ]}
          />
          <table className="w-full text-sm">
            <caption className="sr-only">Evidence gates</caption>
            <thead>
              <tr className="text-left text-ink-muted">
                <th>Gate</th>
                <th>Owner</th>
                <th>Result</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {r.gates.map((g) => (
                <tr key={g.gate} className="border-t border-border">
                  <td>
                    <Mono>{g.gate}</Mono> {g.title}
                  </td>
                  <td>{g.owner_role}</td>
                  <td>{g.status ? <Verdict ok={g.status === "PASSED"} yes="Passed" no="Failed" /> : <Badge>Not recorded</Badge>}</td>
                  <td>
                    <Mono>{g.evidence_ref ?? "-"}</Mono>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {r.status === "DRAFT" ? (
            <>
              <form
                aria-label="Record a gate"
                className="grid gap-2 border-t border-border pt-4 sm:grid-cols-5 sm:items-end"
                onSubmit={(e) => {
                  e.preventDefault();
                  attest.mutate(r);
                }}
              >
                <Select label="Gate" value={gate} onChange={setGate} options={r.gates.map((g) => ({ id: g.gate, label: g.gate }))} />
                <Select label="As" value={gateRole} onChange={(v) => setGateRole(GATE_ROLES.find((x) => x === v) ?? "legal")} options={GATE_ROLES.map((x) => ({ id: x, label: x }))} />
                <Select
                  label="Result"
                  value={gateStatus}
                  onChange={(v) => setGateStatus(v === "FAILED" ? "FAILED" : "PASSED")}
                  options={[
                    { id: "PASSED", label: "Passed" },
                    { id: "FAILED", label: "Failed" },
                  ]}
                />
                <TextField label="Evidence reference" value={evidence} onChange={setEvidence} isRequired />
                <Button type="submit" pending={attest.isPending} isDisabled={evidence.trim().length < 3}>
                  Record
                </Button>
              </form>
              <div className="space-y-2 border-t border-border pt-4">
                <TextField
                  label="Suppliers to pin"
                  description="One per line: entity/scheme/country/service, e.g. acme-idv/UK_DIATF/GB/IDV. Each must be certified on its official register now."
                  value={suppliers}
                  onChange={setSuppliers}
                  multiline
                  rows={3}
                  mono
                />
                <Button variant="secondary" onPress={() => freeze.mutate(r)} pending={freeze.isPending}>
                  Freeze release
                </Button>
              </div>
            </>
          ) : null}
          {r.status === "FROZEN" ? (
            <div className="flex flex-wrap items-end gap-2 border-t border-border pt-4">
              <Select
                label="Approve as"
                value={approver}
                onChange={(v) => setApprover(APPROVERS.find((x) => x === v) ?? "legal")}
                options={APPROVERS.filter((x) => r.missing_approvals.includes(x)).map((x) => ({ id: x, label: x }))}
              />
              <Button onPress={() => approve.mutate(r)} pending={approve.isPending} isDisabled={!r.missing_approvals.includes(approver)}>
                Sign this hash
              </Button>
            </div>
          ) : null}
          {r.status === "RELEASEABLE" ? (
            <Button onPress={() => launch.mutate(r)} pending={launch.isPending}>
              Launch market
            </Button>
          ) : null}
          {r.status === "LAUNCHED" ? (
            <Button variant="danger" onPress={() => withdraw.mutate(r)} pending={withdraw.isPending}>
              Withdraw market now
            </Button>
          ) : null}
          {error ? <RegError error={error} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

function MarketStatusCheck() {
  const [f, setF] = useState({ country: "", product: "INDIVIDUAL_VERIFICATION", purpose: "ENGAGEMENT" });
  const [asked, setAsked] = useState<typeof f | null>(null);
  const q = useCellQuery(["regulatory", "market-status", asked], (api) => api.regulatory.marketStatus(asked!.country, asked!.product, asked!.purpose), {
    enabled: asked !== null,
  });
  return (
    <Card title="Is this market open now?">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setAsked({ country: upper(f.country), product: upper(f.product), purpose: upper(f.purpose) });
        }}
      >
        <TextField label="Country" value={f.country} onChange={(v) => setF((p) => ({ ...p, country: v }))} className="w-24" />
        <TextField label="Product" value={f.product} onChange={(v) => setF((p) => ({ ...p, product: v }))} mono />
        <TextField label="Purpose" value={f.purpose} onChange={(v) => setF((p) => ({ ...p, purpose: v }))} mono />
        <Button type="submit" variant="secondary" isDisabled={f.country.trim().length !== 2}>
          Check
        </Button>
      </form>
      {asked ? (
        <div className="mt-3">
          <QueryView query={q}>
            {(d) => (
              <p className="flex items-center gap-2 text-sm">
                <Verdict ok={d.open} yes="Open" no="Closed" /> <Mono>{d.reason}</Mono>
                {d.release ? <span className="text-ink-muted">by {`${d.release.release_id} v${d.release.version}`}</span> : null}
              </p>
            )}
          </QueryView>
        </div>
      ) : null}
    </Card>
  );
}

export function ReleasesPage() {
  const q = useCellQuery(REG.releases, (api) => api.regulatory.releases());
  const [creating, setCreating] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const keyOf = (r: MarketRelease) => `${r.release_id}:${r.version}`;
  const open = q.data?.items.find((x) => keyOf(x) === openKey) ?? null;
  const setOpen = (x: MarketRelease | null) => setOpenKey(x ? keyOf(x) : null);
  const columns: Column<MarketRelease>[] = [
    { key: "id", header: "Release", pinned: true, render: (r) => <Mono>{`${r.release_id} v${r.version}`}</Mono>, sortValue: (r) => r.release_id },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Release" /> },
    { key: "market", header: "Market", render: (r) => `${r.market.country} · ${r.market.product} · ${r.market.purpose}`, sortValue: (r) => r.market.country },
    { key: "gates", header: "Gates passed", render: (r) => `${r.gates.filter((g) => g.status === "PASSED").length}/${r.gates.length}` },
    { key: "approvals", header: "Approvals", render: (r) => `${APPROVERS.length - r.missing_approvals.length}/${APPROVERS.length}` },
    {
      key: "open",
      header: "Actions",
      align: "end",
      render: (r) => (
        <Button size="xs" onPress={() => setOpen(r)} aria-label={`Open ${r.release_id} v${r.version}`}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Market releases"
        description="A market is launched market by market (WF-08), never globally. The author records nothing and approves nothing; gate recorders cannot approve."
        actions={<Button onPress={() => setCreating(true)}>Open release</Button>}
      />
      <div className="space-y-5">
        <MarketStatusCheck />
        <Card padded={false} title="Releases">
          <QueryView query={q}>
            {(d) => (
              <DataTable
                caption="Market releases"
                columns={columns}
                rows={d.items}
                rowKey={(r) => `${r.release_id}:${r.version}`}
                empty={
                  <EmptyState icon={Rocket} title="No releases">
                    No market has been released.
                  </EmptyState>
                }
              />
            )}
          </QueryView>
        </Card>
      </div>
      <OpenReleaseDialog isOpen={creating} onClose={() => setCreating(false)} />
      <ReleaseDialog release={open} onClose={() => setOpen(null)} />
    </>
  );
}
