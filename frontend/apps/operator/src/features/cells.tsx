"use client";

import type { CellReadiness } from "@zoiko/api-client";
import { Banner, Button, Card, Checkbox, DataTable, Dialog, PageHeader, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Server } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, listInput, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint } from "./recovery-shared";

/** The cells of the reference catalogue (backend/config/cells) plus the local development cell. */
const DEFAULT_CELLS = "local-1, uk-1, eu-1, eu-1-dr";

function num(v: number | null | undefined, unit: string): string {
  return v === null || v === undefined ? "—" : `${v}${unit}`;
}

function ReadinessDialog({ cell, onClose }: { cell: CellReadiness; onClose: () => void }) {
  const [lag, setLag] = useState(cell.replica_lag_s?.toString() ?? "");
  const [backup, setBackup] = useState(cell.backup_age_s?.toString() ?? "");
  const [head, setHead] = useState(cell.one_az_loss_headroom?.toString() ?? "");
  const [keys, setKeys] = useState(!!cell.recovery_keys_ready);
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const bad = [lag, backup].some((v) => v.trim() !== "" && !(Number(v) >= 0)) || (head.trim() !== "" && !(Number(head) >= 0 && Number(head) <= 1));
  const save = useCellMutation(
    (api) => api.platform.reportReadiness(cell.cell_id, { replica_lag_s: n(lag), backup_age_s: n(backup), one_az_loss_headroom: n(head), recovery_keys_ready: keys }),
    { invalidate: [["platform", "readiness"]], success: "Readiness recorded", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title={`Readiness of ${cell.cell_id}`}
      description="What failover gates read: replica lag against the RPO, recovery keys and capacity after losing an availability zone."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={bad} onPress={() => save.mutate(undefined)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <TextField label="Replica lag (s)" inputMode="decimal" value={lag} onChange={setLag} />
          <TextField label="Backup age (s)" inputMode="decimal" value={backup} onChange={setBackup} />
          <TextField label="One-AZ-loss headroom (0–1)" inputMode="decimal" value={head} onChange={setHead} />
        </div>
        <Checkbox isSelected={keys} onChange={setKeys}>
          Recovery KMS/HSM key path available
        </Checkbox>
        {save.error ? <><ApiErrorView error={save.error} /><CapabilityHint error={save.error} capability="platform.cell.manage" /></> : null}
      </div>
    </Dialog>
  );
}

/** One cell's health in full, including whether a recovery is running (the list shows readiness only). */
function HealthDialog({ cell, onClose }: { cell: CellReadiness; onClose: () => void }) {
  const health = useCellQuery(["platform", "cell-health", cell.cell_id], (api) => api.ops.cellHealth(cell.cell_id));
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={`Health of ${cell.cell_id}`}
      footer={
        <Button variant="secondary" onPress={onClose}>
          Close
        </Button>
      }
    >
      <QueryView query={health} loadingRows={2}>
        {(h) => (
          <div className="space-y-3 text-sm">
            <p className="flex items-center gap-2">
              State <StatusBadge state={h.state} kind="Cell" size="xs" />
            </p>
            {h.recovery_in_progress ? (
              <Banner tone="warn" title="A recovery is running on this cell">
                Do not start a second one; follow the running one under Recovery.
              </Banner>
            ) : (
              <p className="text-ink-muted">No recovery is running on this cell.</p>
            )}
            <dl className="grid grid-cols-2 gap-2">
              <dt className="text-ink-muted">Replica lag</dt>
              <dd>{num(h.replica_lag_s, " s")}</dd>
              <dt className="text-ink-muted">Backup age</dt>
              <dd>{num(h.backup_age_s, " s")}</dd>
              <dt className="text-ink-muted">Recovery keys</dt>
              <dd>{h.reported === false ? "Not reported" : h.recovery_keys_ready ? "Ready" : "Not ready"}</dd>
            </dl>
          </div>
        )}
      </QueryView>
    </Dialog>
  );
}

function StateDialog({ cell, onClose }: { cell: CellReadiness; onClose: () => void }) {
  const quarantined = cell.status === "QUARANTINED";
  const [reason, setReason] = useState("");
  const change = useCellMutation(
    (api) => (quarantined ? api.platform.restoreCell(cell.cell_id, reason.trim()) : api.platform.quarantineCell(cell.cell_id, reason.trim())),
    { invalidate: [["platform", "readiness"]], success: quarantined ? "Cell restored" : "Cell quarantined", onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !change.isPending && onClose()}
      title={quarantined ? `Restore ${cell.cell_id}` : `Quarantine ${cell.cell_id}`}
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button variant={quarantined ? "primary" : "danger"} pending={change.isPending} isDisabled={reason.trim().length < 4} onPress={() => change.mutate(undefined)}>
            {quarantined ? "Restore" : "Quarantine"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-ink-muted">
          {quarantined
            ? "The cell accepts new companies again and can be a recovery target."
            : "The cell stops accepting new companies and is never chosen as a recovery target. Tenants already there move only through a declared failover."}
        </p>
        <TextField label="Reason" isRequired multiline rows={2} value={reason} onChange={setReason} description="Kept as a commitment in the audit record." />
        {change.error ? <><ApiErrorView error={change.error} /><CapabilityHint error={change.error} capability="platform.cell.manage" /></> : null}
      </div>
    </Dialog>
  );
}

/** Cells and recovery readiness (RESILIENCE-001 sections 25, 28). */
export function CellsPage() {
  const [cellsText, setCellsText] = useState(DEFAULT_CELLS);
  const cells = listInput(cellsText).slice(0, 20);
  const readiness = useCellQuery(["platform", "readiness", cells.join(",")], (api) => api.platform.readiness(cells), {
    enabled: cells.length > 0,
    refetchInterval: 30_000,
  });
  const [dialog, setDialog] = useState<{ kind: "readiness" | "state" | "health"; cell: CellReadiness } | null>(null);
  const columns: Column<CellReadiness>[] = [
    { key: "cell", header: "Cell", pinned: true, render: (c) => <span className="font-mono text-sm">{c.cell_id}</span> },
    { key: "status", header: "State", render: (c) => <StatusBadge state={c.status} kind="Cell" size="xs" /> },
    { key: "lag", header: "Replica lag", render: (c) => num(c.replica_lag_s, " s") },
    { key: "backup", header: "Backup age", render: (c) => num(c.backup_age_s, " s") },
    { key: "keys", header: "Recovery keys", render: (c) => (c.reported === false ? "—" : c.recovery_keys_ready ? "Ready" : "Not ready") },
    { key: "head", header: "AZ-loss headroom", render: (c) => (c.one_az_loss_headroom === null || c.one_az_loss_headroom === undefined ? "—" : `${Math.round(c.one_az_loss_headroom * 100)}%`) },
    {
      key: "a",
      header: "",
      align: "end",
      render: (c) => (
        <div className="flex justify-end gap-2">
          <Button size="xs" variant="ghost" onPress={() => setDialog({ kind: "health", cell: c })}>
            Health
          </Button>
          <Button size="xs" variant="secondary" onPress={() => setDialog({ kind: "readiness", cell: c })}>
            Report readiness
          </Button>
          <Button size="xs" variant={c.status === "QUARANTINED" ? "primary" : "ghost"} onPress={() => setDialog({ kind: "state", cell: c })}>
            {c.status === "QUARANTINED" ? "Restore" : "Quarantine"}
          </Button>
        </div>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        icon={Server}
        title="Cells"
        description="Each cell's state and recovery readiness. Failover gates read these numbers; a missing report blocks failover to that cell."
      />
      <Banner tone="info" className="mb-6">
        Changing a cell needs an ACTIVE <span className="font-mono">platform.cell.manage</span> grant. Request it under My access.
      </Banner>
      <Card
        padded={false}
        title="Readiness"
        actions={<TextField label="Cells" mono value={cellsText} onChange={setCellsText} className="min-w-80" />}
      >
        <QueryView query={readiness}>
          {(r) => <DataTable caption="Cell readiness" columns={columns} rows={r.cells} rowKey={(c) => c.cell_id} />}
        </QueryView>
      </Card>
      {dialog?.kind === "readiness" ? <ReadinessDialog cell={dialog.cell} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "state" ? <StateDialog cell={dialog.cell} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "health" ? <HealthDialog cell={dialog.cell} onClose={() => setDialog(null)} /> : null}
    </>
  );
}
