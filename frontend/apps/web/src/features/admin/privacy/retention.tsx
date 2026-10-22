"use client";

import type { PrivacyRow } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, Dialog, EmptyState, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Clock, Plus, Search } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { cellValue, PRIVACY_KEY } from "./shared";

const RETENTION_KEY = [...PRIVACY_KEY, "retention"];

export function RetentionSection() {
  const policies = useCellQuery(RETENTION_KEY, (api) => api.privacy.admin.retention());
  const packs = useCellQuery([...PRIVACY_KEY, "packs"], (api) => api.privacy.packs());
  const [creating, setCreating] = useState(false);
  const [subject, setSubject] = useState("");
  const [lookup, setLookup] = useState("");
  const dataMap = useCellQuery([...PRIVACY_KEY, "data-map", lookup], (api) => api.privacy.admin.dataMap(lookup), { enabled: lookup.length === 36 });

  const cols: Column<PrivacyRow>[] = [
    { key: "policy_code", header: "Policy", pinned: true, render: (r) => <span className="font-mono text-xs">{String(r.policy_code)} v{String(r.version)}</span> },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={String(r.status)} kind="Retention policy" /> },
    { key: "object_type", header: "Objects", render: (r) => cellValue(r.object_type) },
    { key: "trigger", header: "Trigger", render: (r) => cellValue(r.retention_trigger) },
    { key: "days", header: "Retain", render: (r) => `${String(r.retention_days)} days` },
    { key: "action", header: "On expiry", render: (r) => cellValue(r.action_on_expiry) },
    {
      key: "backup",
      header: "Backup erasure window",
      render: (r) => (r.backup_erasure_window_days === null ? <Badge tone="warn">DPO pending</Badge> : `${String(r.backup_erasure_window_days)} days`),
    },
    { key: "legal", header: "Legally required", render: (r) => cellValue(r.legally_required) },
  ];
  const mapCols: Column<PrivacyRow>[] = [
    { key: "store", header: "Store", pinned: true, render: (r) => <span className="font-mono text-xs">{String(r.store)}</span> },
    { key: "object_type", header: "Object", render: (r) => cellValue(r.object_type) },
    { key: "purpose_code", header: "Purpose", render: (r) => cellValue(r.purpose_code) },
    { key: "data_category", header: "Category", render: (r) => cellValue(r.data_category) },
    { key: "region", header: "Region", render: (r) => cellValue(r.region) },
    { key: "due_at", header: "Due", render: (r) => cellValue(r.due_at) },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={String(r.status)} kind="Data map entry" /> },
  ];

  return (
    <div className="space-y-5">
      <Card padded={false} title="Jurisdiction packs" description="Counsel-approved, versioned legal configuration. Engineering never fills a counsel field.">
        <QueryView query={packs}>
          {(d) => (
            <ul className="divide-y divide-border">
              {d.items.map((p) => (
                <li key={p.pack_id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-sm">
                  <span>
                    <span className="font-mono">{p.pack_id}</span> <span className="text-ink-subtle">v{p.version} · {p.framework}</span>
                  </span>
                  <span className="flex gap-1.5">
                    <Badge tone={p.deployable ? "ok" : "warn"}>{p.deployable ? "Approved" : "Not approved by counsel"}</Badge>
                    {!p.deadlines_configured ? <Badge tone="warn">Deadlines: counsel pending</Badge> : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </Card>
      <Card
        padded={false}
        title="Retention policies"
        description="Executable schedules. Due dates are fixed from the original trigger; a released hold never restarts the clock."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Publish policy
          </Button>
        }
      >
        <QueryView query={policies}>
          {(d) => <DataTable caption="Retention policies" columns={cols} rows={d.items} rowKey={(r) => String(r.retention_policy_id)} empty={<EmptyState icon={Clock} title="No retention policies" />} />}
        </QueryView>
      </Card>
      <Card
        padded={false}
        title="Subject data map"
        description="Where one subject's personal data lives, under which purpose and policy (authorized, audited lookup)."
        actions={
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setLookup(subject.trim());
            }}
          >
            <TextField label="Subject principal ID" value={subject} onChange={setSubject} mono />
            <Button size="sm" variant="secondary" type="submit" icon={<Search aria-hidden className="size-4" />}>
              Look up
            </Button>
          </form>
        }
      >
        {lookup.length === 36 ? (
          <QueryView query={dataMap}>
            {(d) => <DataTable caption="Data map" columns={mapCols} rows={d.items} rowKey={(r) => String(r.entry_id)} empty={<EmptyState icon={Search} title="No registered personal data for this subject" />} />}
          </QueryView>
        ) : (
          <p className="px-5 py-4 text-sm text-ink-muted">Enter a subject ID to see their data map.</p>
        )}
      </Card>
      <RetentionDialog isOpen={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function RetentionDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [objectType, setObjectType] = useState("");
  const [pack, setPack] = useState("uk-gdpr");
  const [trigger, setTrigger] = useState("CREATED_AT");
  const [days, setDays] = useState("365");
  const [action, setAction] = useState("CRYPTO_SHRED");
  const [backup, setBackup] = useState("");
  const [legal, setLegal] = useState("no");
  const [approval, setApproval] = useState("");
  const create = useCellMutation(
    (api) =>
      api.privacy.admin.createRetention({
        code: code.trim(),
        object_type: objectType.trim(),
        jurisdiction_pack_id: pack,
        trigger,
        retention_days: Number(days),
        action,
        backup_erasure_window_days: backup.trim() ? Number(backup) : null,
        legal_hold_suspends: true,
        legally_required: legal === "yes",
        purpose_code: null,
        approval_request_id: approval.trim(),
      }),
    { invalidate: [RETENTION_KEY], success: "Retention policy published", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Publish a retention policy"
      description="Governed: a second administrator approves the exact schedule (code, object type, days, action, backup window)."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!code || !objectType || !approval.trim() || Number.isNaN(Number(days))} onPress={() => create.mutate(undefined)}>
            Publish
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <RiskBadge risk="R3" />
        </div>
        <TextField label="Policy code" value={code} onChange={(v) => setCode(v.toUpperCase())} isRequired mono />
        <TextField label="Object type" value={objectType} onChange={setObjectType} isRequired mono placeholder="verification.document" />
        <TextField label="Jurisdiction pack" value={pack} onChange={setPack} mono />
        <Select
          label="Trigger"
          value={trigger}
          onChange={setTrigger}
          options={["CREATED_AT", "LAST_USED", "VERIFIED_AT", "CONTRACT_END", "ACCOUNT_CLOSE", "GRANT_EXPIRY", "EVENT_DATE"].map((t) => ({ id: t, label: t }))}
        />
        <TextField label="Retention (days)" value={days} onChange={setDays} inputMode="numeric" />
        <Select label="Action on expiry" value={action} onChange={setAction} options={["DELETE", "CRYPTO_SHRED", "ANONYMIZE", "ARCHIVE_RESTRICTED"].map((a) => ({ id: a, label: a }))} />
        <TextField label="Backup erasure window (days)" value={backup} onChange={setBackup} inputMode="numeric" description="The DPO's number; leave empty until they set it." />
        <Select label="Legally required retention" value={legal} onChange={setLegal} options={[{ id: "no", label: "No" }, { id: "yes", label: "Yes: survives erasure requests (restricted)" }]} />
        <TextField label="Approval request ID" value={approval} onChange={setApproval} isRequired mono className="sm:col-span-2" />
      </div>
      {create.error ? <ApiErrorView error={create.error} /> : null}
    </Dialog>
  );
}
