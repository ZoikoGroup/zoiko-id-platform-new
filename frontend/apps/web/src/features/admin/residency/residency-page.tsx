"use client";

import type { ResidencyMatrixRow, TransferDecision, TransferRule } from "@zoiko/api-client";
import {
  Badge,
  Banner,
  Button,
  Card,
  DataTable,
  DateTime,
  Dialog,
  EmptyState,
  KeyValues,
  PageHeader,
  RiskBadge,
  Select,
  StatusBadge,
  Tabs,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Globe2, Plus } from "lucide-react";
import { useState } from "react";

import { GuidedApproval } from "@/features/approvals/guided-approval";
import { useTenant } from "@/features/onboarding/shared";
import { ApiErrorView, listInput, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const RES_KEY = ["admin", "residency"];
const RULES_KEY = [...RES_KEY, "rules"];

export function ResidencyPage() {
  return (
    <>
      <PageHeader
        title="Residency & sovereignty"
        description="Where your data lives, where it may be recovered, and the only approved ways restricted data may leave its boundary (cross-border default: deny)."
      />
      <Tabs
        label="Residency"
        tabs={[
          { id: "profile", label: "Profile", content: <ProfileSection /> },
          { id: "rules", label: "Transfer rules", content: <RulesSection /> },
          { id: "matrix", label: "Placement matrix", content: <MatrixSection /> },
        ]}
      />
    </>
  );
}

function ProfileSection() {
  const current = useCellQuery([...RES_KEY, "current"], (api) => api.residency.current());
  const cells = useCellQuery([...RES_KEY, "cells"], (api) => api.residency.cells());
  const [editing, setEditing] = useState(false);
  return (
    <div className="space-y-5">
      <Card
        title="Residency profile"
        actions={
          <Button size="sm" variant="secondary" onPress={() => setEditing(true)}>
            Change profile
          </Button>
        }
      >
        <QueryView query={current}>
          {(d) =>
            d.residency ? (
              <KeyValues
                columns={3}
                items={[
                  ["Profile", <Badge key="p" tone="brand">{d.residency.residency_profile_id}</Badge>],
                  ["Home cell", <span key="h" className="font-mono">{d.residency.home_cell_id}</span>],
                  ["Recovery cells", d.residency.permitted_recovery_cells.join(", ") || "None (in-region recovery only)"],
                  ["Operator jurisdictions", d.residency.operator_jurisdictions.join(", ") || "Unrestricted"],
                  ["Key profile", d.residency.key_profile],
                  ["Jurisdiction pack", d.residency.jurisdiction_pack_id],
                  ["Placement epoch", d.residency.placement_epoch],
                  ["Revision", d.residency.revision],
                ]}
              />
            ) : (
              <Banner tone="warn" title="No residency profile assigned">Restricted data cannot be transferred out of the cell until one is assigned.</Banner>
            )
          }
        </QueryView>
      </Card>
      <Card padded={false} title="Cells" description="Stable deployment identities and the residency profiles each supports.">
        <QueryView query={cells}>
          {(d) => (
            <DataTable
              caption="Cells"
              rows={d.items}
              rowKey={(c) => c.cell_id}
              columns={[
                { key: "cell", header: "Cell", pinned: true, render: (c) => <span className="font-mono">{c.cell_id}</span> },
                { key: "j", header: "Jurisdiction", render: (c) => `${c.jurisdiction ?? "—"} (${c.legal_zone ?? "—"})` },
                { key: "region", header: "Region", render: (c) => <span className="font-mono text-xs">{c.region}</span> },
                { key: "profiles", header: "Profiles", render: (c) => c.supported_profiles.join(", ") || "—" },
                { key: "recovery", header: "Recovery", render: (c) => (c.recovery_only ? <Badge tone="info">recovery only</Badge> : c.approved_recovery_cells.join(", ") || "—") },
              ]}
            />
          )}
        </QueryView>
      </Card>
      <ProfileDialog isOpen={editing} onClose={() => setEditing(false)} />
    </div>
  );
}

const PROFILE_LABELS: Record<string, string> = {
  "SRP-0": "SRP-0 Shared global",
  "SRP-1": "SRP-1 Regional resident",
  "SRP-2": "SRP-2 Sovereign controlled",
  "SRP-3": "SRP-3 Dedicated sovereign",
};
const PACKS = [
  { id: "uk-gdpr", label: "UK GDPR" },
  { id: "eu-gdpr", label: "EU GDPR" },
  { id: "us-ca-ccpa", label: "California CCPA" },
];

/** The legal pack that normally goes with a cell's legal zone, so the form starts on a sensible choice. */
function packFor(zone: string | null | undefined): string {
  return zone === "EU" ? "eu-gdpr" : zone === "US" ? "us-ca-ccpa" : "uk-gdpr";
}

function ProfileDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const tenant = useTenant();
  const cells = useCellQuery([...RES_KEY, "cells"], (api) => api.residency.cells());
  // A company's profile is placed on the cell that serves it. Moving to another cell is a migration, not a profile
  // change, so only the company's own cell is offered.
  const ownCell = tenant.data?.home_cell_id;
  const usable = (cells.data?.items ?? []).filter(
    (c) => !c.recovery_only && c.supported_profiles.length > 0 && (!ownCell || c.cell_id === ownCell),
  );
  // Start from the company's own home cell: the onboarding gate needs the profile placed there.
  const [homeChoice, setHomeChoice] = useState<string | null>(null);
  const home = homeChoice ?? tenant.data?.home_cell_id ?? usable[0]?.cell_id ?? "";
  const cell = usable.find((c) => c.cell_id === home);
  const profiles = cell?.supported_profiles ?? [];
  const [profileChoice, setProfileChoice] = useState<string | null>(null);
  const profile = profileChoice && profiles.includes(profileChoice) ? profileChoice : (profiles.includes("SRP-1") ? "SRP-1" : (profiles[0] ?? "SRP-1"));
  const [recovery, setRecovery] = useState("");
  const [operators, setOperators] = useState("");
  const [packChoice, setPackChoice] = useState<string | null>(null);
  const pack = packChoice ?? packFor(cell?.legal_zone);
  const [legal, setLegal] = useState("");

  const recoveryCells = listInput(recovery);
  const operatorList = listInput(operators).map((j) => j.toUpperCase());
  // The exact change the approver is shown and approves; it must match what the server binds to.
  const change = [
    profile,
    home,
    [...recoveryCells].sort().join(","),
    [...operatorList].sort().join(","),
    pack.trim(),
  ].join("|");
  const summary = `Set the company's residency to ${PROFILE_LABELS[profile] ?? profile}, home cell ${home}, ${PACKS.find((p) => p.id === pack)?.label ?? pack}${recoveryCells.length ? `, recovery in ${recoveryCells.join(", ")}` : ""}.`;

  const save = useCellMutation(
    (api, approvalRequestId: string) =>
      api.residency.set({
        residency_profile_id: profile as "SRP-0" | "SRP-1" | "SRP-2" | "SRP-3",
        home_cell_id: home.trim(),
        permitted_recovery_cells: recoveryCells,
        prohibited_cells: [],
        operator_jurisdictions: operatorList,
        key_profile: "PLATFORM_MANAGED",
        jurisdiction_pack_id: pack.trim(),
        approval_ref: legal.trim(),
        approval_request_id: approvalRequestId,
      }),
    { invalidate: [RES_KEY], success: "Residency profile updated", onSuccess: onClose },
  );
  const ready = legal.trim().length >= 3 && home.length > 0;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Change the residency profile"
      description="Where your company's data is kept. A second administrator has to approve this exact change."
      size="lg"
      footer={
        <Button variant="secondary" onPress={onClose}>
          Close
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <RiskBadge risk="R3" />
        </div>
        <Select
          label="Home cell"
          description="Where your data lives. Your company's own cell is chosen for you."
          value={home}
          onChange={(v) => {
            setHomeChoice(v);
            setProfileChoice(null);
            setPackChoice(null);
          }}
          options={usable.map((c) => ({ id: c.cell_id, label: `${c.cell_id} · ${c.legal_zone ?? c.jurisdiction ?? ""}` }))}
        />
        <Select
          label="Profile"
          description="Only profiles this cell supports are offered."
          value={profile}
          onChange={setProfileChoice}
          options={profiles.map((p) => ({ id: p, label: PROFILE_LABELS[p] ?? p }))}
        />
        <Select label="Legal pack" value={pack} onChange={setPackChoice} options={PACKS} />
        <TextField label="Recovery cells (optional)" value={recovery} onChange={setRecovery} mono description="Leave empty unless your cell lists approved recovery cells." />
        {profile === "SRP-2" || profile === "SRP-3" ? (
          <TextField label="Operator jurisdictions" value={operators} onChange={setOperators} mono description="Country codes, e.g. GB. Required for this profile." />
        ) : null}
        <TextField
          label="Legal mapping reference"
          value={legal}
          onChange={setLegal}
          isRequired
          mono
          className="sm:col-span-2"
          description="Your legal team's reference for this choice, for the record (at least 3 characters)."
        />
      </div>
      <div className="mt-5">
        {ready ? (
          <GuidedApproval action="tenant.residency.manage" resource="tenant_residency" change={change} summary={summary} label="Apply profile">
            {(id) => (
              <Button pending={save.isPending} onPress={() => save.mutate(id)}>
                Apply profile
              </Button>
            )}
          </GuidedApproval>
        ) : (
          <p className="text-sm text-ink-muted">Fill in the legal mapping reference to ask for approval.</p>
        )}
      </div>
      {save.error ? <ApiErrorView error={save.error} /> : null}
    </Dialog>
  );
}

function RulesSection() {
  const rules = useCellQuery(RULES_KEY, (api) => api.residency.rules());
  const [adding, setAdding] = useState(false);
  const [decision, setDecision] = useState<TransferDecision | null>(null);
  const [dc, setDc] = useState("DC2");
  const [dest, setDest] = useState("");
  const [purpose, setPurpose] = useState("");
  const suspend = useCellMutation((api, id: string) => api.residency.suspendRule(id), { invalidate: [RULES_KEY], success: "Transfer rule suspended" });
  const check = useCellMutation((api) => api.residency.checkTransfer({ data_class: dc, destination_cell_id: dest.trim(), purpose_code: purpose.trim() }), {
    onSuccess: setDecision,
  });
  const columns: Column<TransferRule>[] = [
    { key: "dc", header: "Data class", pinned: true, render: (r) => <Badge tone={r.data_class === "DC3" ? "danger" : "warn"}>{r.data_class}</Badge> },
    { key: "dest", header: "Destination", render: (r) => <span className="font-mono">{r.destination_cell_id}</span> },
    { key: "purpose", header: "Purpose", render: (r) => <span className="font-mono text-xs">{r.purpose_code}</span> },
    { key: "mech", header: "Mechanism", render: (r) => r.mechanism },
    { key: "fields", header: "Fields", render: (r) => <span className="font-mono text-2xs">{r.field_allowlist.join(", ") || "—"}</span> },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Transfer rule" /> },
    { key: "valid", header: "Valid until", render: (r) => (r.valid_to ? <DateTime value={r.valid_to} style="date" /> : "Open-ended") },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.status === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" pending={suspend.isPending} onPress={() => suspend.mutate(r.rule_id)}>
            Suspend
          </Button>
        ) : null,
    },
  ];
  return (
    <div className="space-y-5">
      <Card
        padded={false}
        title="Approved transfer rules"
        description="An exact data class, destination and purpose under a mechanism your jurisdiction pack supports. Suspending one takes effect at once."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
            New rule
          </Button>
        }
      >
        <QueryView query={rules}>
          {(d) => <DataTable caption="Transfer rules" columns={columns} rows={d.items} rowKey={(r) => r.rule_id} empty={<EmptyState icon={Globe2} title="No transfer rules">Restricted data stays inside its residency boundary.</EmptyState>} />}
        </QueryView>
      </Card>
      <Card title="Check a transfer" description="Dry-run the gateway decision. Restricted decisions, allowed or denied, are recorded as evidence.">
        <div className="grid gap-3 sm:grid-cols-4 sm:items-end">
          <Select label="Data class" value={dc} onChange={setDc} options={["DC0", "DC1", "DC2", "DC3"].map((d) => ({ id: d, label: d }))} />
          <TextField label="Destination cell" value={dest} onChange={setDest} mono />
          <TextField label="Purpose code" value={purpose} onChange={(v) => setPurpose(v.toUpperCase())} mono />
          <Button size="sm" variant="secondary" pending={check.isPending} isDisabled={!dest || !purpose} onPress={() => check.mutate(undefined)}>
            Check
          </Button>
        </div>
        {check.error ? <ApiErrorView error={check.error} /> : null}
        {decision ? (
          <Banner className="mt-4" tone={decision.allowed ? "ok" : "danger"} title={decision.allowed ? "Transfer allowed" : `Transfer denied: ${decision.code}`}>
            {decision.allowed && decision.field_allowlist.length ? `Only these fields may cross: ${decision.field_allowlist.join(", ")}.` : null}
            {decision.cross_border ? " This is a cross-border transfer." : ""}
          </Banner>
        ) : null}
      </Card>
      <RuleDialog isOpen={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

function RuleDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [dc, setDc] = useState("DC2");
  const [dest, setDest] = useState("");
  const [purpose, setPurpose] = useState("");
  const [mechanism, setMechanism] = useState("");
  const [fields, setFields] = useState("");
  const [legal, setLegal] = useState("");
  const [approval, setApproval] = useState("");
  const add = useCellMutation(
    (api) =>
      api.residency.addRule({
        data_class: dc,
        destination_cell_id: dest.trim(),
        purpose_code: purpose.trim(),
        mechanism: mechanism.trim(),
        field_allowlist: listInput(fields),
        approval_ref: legal.trim(),
        approval_request_id: approval.trim(),
        valid_to: null,
      }),
    { invalidate: [RULES_KEY], success: "Transfer rule approved", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Approve a transfer rule"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={add.isPending} isDisabled={!dest || !purpose || !mechanism || !legal || !approval} onPress={() => add.mutate(undefined)}>
            Approve rule
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Select label="Data class" value={dc} onChange={setDc} options={["DC1", "DC2", "DC3"].map((d) => ({ id: d, label: d }))} />
        <TextField label="Destination cell" value={dest} onChange={setDest} mono isRequired />
        <TextField label="Purpose code" value={purpose} onChange={(v) => setPurpose(v.toUpperCase())} mono isRequired />
        <TextField label="Transfer mechanism" value={mechanism} onChange={setMechanism} mono isRequired description="e.g. uk_idta, eu_sccs (from your pack)" />
        <TextField label="Field allowlist" value={fields} onChange={setFields} mono className="sm:col-span-2" />
        <TextField label="Legal approval reference" value={legal} onChange={setLegal} mono isRequired />
        <TextField label="Approval request ID" value={approval} onChange={setApproval} mono isRequired />
      </div>
      {add.error ? <ApiErrorView error={add.error} /> : null}
    </Dialog>
  );
}

function MatrixSection() {
  const matrix = useCellQuery([...RES_KEY, "matrix"], (api) => api.residency.matrix());
  const columns: Column<ResidencyMatrixRow>[] = [
    { key: "subsystem", header: "Subsystem", pinned: true, render: (r) => r.subsystem.replaceAll("_", " ") },
    { key: "cell", header: "Cell", render: (r) => <span className="font-mono">{r.cell_id}</span> },
    { key: "region", header: "Region", render: (r) => <span className="font-mono text-xs">{r.region}</span> },
    { key: "j", header: "Jurisdiction", render: (r) => `${r.jurisdiction ?? "—"} (${r.legal_zone ?? "—"})` },
    { key: "recovery", header: "Recovery", render: (r) => r.recovery_cells.join(", ") || "In-region" },
    { key: "evidence", header: "Evidence", render: (r) => <Badge tone="neutral">{r.evidence}</Badge> },
  ];
  return (
    <Card padded={false} title="Residency coverage matrix" description="Every in-scope subsystem must satisfy the profile before a residency claim is made (TENANCY §6).">
      <QueryView query={matrix} notFound={<p className="px-5 py-4 text-sm text-ink-muted">Assign a residency profile first.</p>}>
        {(d) => <DataTable caption="Coverage matrix" columns={columns} rows={d.items} rowKey={(r) => r.subsystem} />}
      </QueryView>
    </Card>
  );
}
