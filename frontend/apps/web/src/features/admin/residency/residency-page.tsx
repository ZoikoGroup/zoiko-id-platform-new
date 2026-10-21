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

function ProfileDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [profile, setProfile] = useState("SRP-1");
  const [home, setHome] = useState("uk-1");
  const [recovery, setRecovery] = useState("");
  const [operators, setOperators] = useState("");
  const [pack, setPack] = useState("uk-gdpr");
  const [legal, setLegal] = useState("");
  const [approval, setApproval] = useState("");
  const save = useCellMutation(
    (api) =>
      api.residency.set({
        residency_profile_id: profile,
        home_cell_id: home.trim(),
        permitted_recovery_cells: listInput(recovery),
        prohibited_cells: [],
        operator_jurisdictions: listInput(operators).map((j) => j.toUpperCase()),
        key_profile: "PLATFORM_MANAGED",
        jurisdiction_pack_id: pack.trim(),
        approval_ref: legal.trim(),
        approval_request_id: approval.trim(),
      }),
    { invalidate: [RES_KEY], success: "Residency profile updated", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Change the residency profile"
      description="A contractual capability, not a self-service label: the cell must support it, Legal must have mapped it, and a second administrator approves this exact change."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={legal.trim().length < 3 || !approval.trim()} onPress={() => save.mutate(undefined)}>
            Apply profile
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <RiskBadge risk="R3" />
        </div>
        <Select
          label="Profile"
          value={profile}
          onChange={setProfile}
          options={[
            { id: "SRP-0", label: "SRP-0 Shared global" },
            { id: "SRP-1", label: "SRP-1 Regional resident" },
            { id: "SRP-2", label: "SRP-2 Sovereign controlled" },
            { id: "SRP-3", label: "SRP-3 Dedicated sovereign" },
          ]}
        />
        <TextField label="Home cell" value={home} onChange={setHome} mono />
        <TextField label="Permitted recovery cells" value={recovery} onChange={setRecovery} mono />
        <TextField label="Operator jurisdictions" value={operators} onChange={setOperators} mono description="Required for SRP-2/3." />
        <TextField label="Jurisdiction pack" value={pack} onChange={setPack} mono />
        <TextField label="Legal mapping approval reference" value={legal} onChange={setLegal} isRequired mono />
        <TextField label="Approval request ID" value={approval} onChange={setApproval} isRequired mono className="sm:col-span-2" />
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
