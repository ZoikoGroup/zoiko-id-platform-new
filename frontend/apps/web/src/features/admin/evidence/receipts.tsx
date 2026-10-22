"use client";

import type { ReceiptSummary, ReplayOutcome, ReceiptVerification } from "@zoiko/api-client";
import {
  Badge,
  Banner,
  Button,
  Card,
  CopyId,
  DataTable,
  DateTime,
  EmptyState,
  EvidenceChip,
  JsonView,
  KeyValues,
  Select,
  SidePanel,
  StatusBadge,
  TextField,
  type Column,
} from "@zoiko/ui";
import { FileCheck2, ScrollText } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

export const RECEIPTS_KEY = ["admin", "evidence", "receipts"];

const REPLAY_TONE: Record<string, "ok" | "warn" | "danger" | "info"> = {
  VERIFIED_MATCH: "ok",
  VERIFIED_STRUCTURAL_ONLY: "info",
  INCOMPLETE_BY_LAWFUL_ERASURE: "warn",
  MISMATCH: "danger",
  UNVERIFIABLE_INTEGRITY_FAILURE: "danger",
};

const EXPORT_MODES = [
  { id: "CUSTOMER_VERIFY", label: "Customer verification", description: "Minimized, tenant-scoped" },
  { id: "AUDITOR", label: "Auditor", description: "Control/audit sample under engagement" },
  { id: "REGULATOR_LEGAL", label: "Regulator / legal", description: "Case-specific, disclosure review required" },
  { id: "INCIDENT", label: "Incident", description: "Forensic chain-of-custody package" },
];

export function ReceiptsSection() {
  const [evidenceClass, setEvidenceClass] = useState<string>("");
  const [outcome, setOutcome] = useState<string>("");
  const receipts = useCellQuery([...RECEIPTS_KEY, evidenceClass, outcome], (api) =>
    api.evidence.receipts({ evidence_class: evidenceClass || undefined, outcome: outcome || undefined, limit: 100 }),
  );
  const [selected, setSelected] = useState<ReceiptSummary | null>(null);

  const columns: Column<ReceiptSummary>[] = [
    {
      key: "action",
      header: "Action",
      pinned: true,
      render: (r) => (
        <span className="inline-flex flex-col">
          <span className="font-mono text-xs">{r.action}</span>
          <span className="text-2xs text-ink-subtle">{r.resource_class}</span>
        </span>
      ),
      sortValue: (r) => r.action,
    },
    { key: "outcome", header: "Outcome", render: (r) => <StatusBadge state={r.outcome} kind="Decision outcome" />, sortValue: (r) => r.outcome },
    { key: "class", header: "Evidence", render: (r) => <EvidenceChip evidenceClass={r.evidence_class} />, sortValue: (r) => r.evidence_class },
    { key: "risk", header: "Risk", render: (r) => <Badge tone={r.risk_class === "R3" ? "danger" : r.risk_class === "R2" ? "warn" : "neutral"}>{r.risk_class}</Badge> },
    { key: "actor", header: "Actor (pseudonymous)", render: (r) => <span className="font-mono text-2xs">{r.actor_handle.slice(0, 18)}…</span> },
    { key: "at", header: "Decided", render: (r) => <DateTime value={r.effective_at} />, sortValue: (r) => r.effective_at },
    { key: "id", header: "Receipt ID", render: (r) => <CopyId value={r.receipt_id} label="receipt ID" />, defaultHidden: true },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Decision receipts"
        description="Every authorization and protected-action decision leaves a pseudonymous, tamper-evident receipt (E1-E3). E0 decisions are counted, never recorded per subject."
        actions={
          <div className="flex flex-wrap gap-2">
            <Select
              label="Evidence class"
              hideLabel
              value={evidenceClass || "ALL"}
              onChange={(v) => setEvidenceClass(v === "ALL" ? "" : v)}
              options={[{ id: "ALL", label: "All classes" }, { id: "E1", label: "E1" }, { id: "E2", label: "E2" }, { id: "E3", label: "E3" }]}
            />
            <Select
              label="Outcome"
              hideLabel
              value={outcome || "ALL"}
              onChange={(v) => setOutcome(v === "ALL" ? "" : v)}
              options={[
                { id: "ALL", label: "All outcomes" },
                { id: "ALLOW", label: "Allow" },
                { id: "DENY", label: "Deny" },
                { id: "STEP_UP", label: "Step-up" },
                { id: "APPROVAL_REQUIRED", label: "Approval required" },
                { id: "STATE_UNAVAILABLE", label: "State unavailable" },
              ]}
            />
          </div>
        }
      >
        <QueryView query={receipts}>
          {(data) => (
            <DataTable
              caption="Decision receipts"
              columns={columns}
              rows={data.items}
              rowKey={(r) => r.receipt_id}
              onRowAction={setSelected}
              selectedKey={selected?.receipt_id ?? null}
              empty={
                <EmptyState icon={ScrollText} title="No receipts yet">
                  Receipts appear as soon as the decision service evaluates an action in this tenant.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
      <ReceiptPanel receipt={selected} onClose={() => setSelected(null)} />
    </>
  );
}

function ReceiptPanel({ receipt, onClose }: { receipt: ReceiptSummary | null; onClose: () => void }) {
  const id = receipt?.receipt_id ?? "";
  const detail = useCellQuery([...RECEIPTS_KEY, "detail", id], (api) => api.evidence.receipt(id), { enabled: Boolean(receipt) });
  const [verification, setVerification] = useState<ReceiptVerification | null>(null);
  const [replay, setReplay] = useState<ReplayOutcome | null>(null);
  const [exportMode, setExportMode] = useState("CUSTOMER_VERIFY");
  const [purpose, setPurpose] = useState("");
  const [caseRef, setCaseRef] = useState("");

  const verify = useCellMutation((api, rid: string) => api.evidence.verify(rid), { onSuccess: setVerification });
  const runReplay = useCellMutation((api, rid: string) => api.evidence.replay(rid), { onSuccess: setReplay });
  const exportPkg = useCellMutation(
    (api, rid: string) => api.evidence.exportPackage({ receipt_id: rid, mode: exportMode, purpose: purpose.trim(), case_ref: caseRef.trim() }),
    {
      success: "Signed evidence package created",
      onSuccess: (pkg) => {
        const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `evidence-${pkg.manifest.package_id}.json`;
        a.click();
        URL.revokeObjectURL(a.href);
      },
    },
  );

  function close() {
    setVerification(null);
    setReplay(null);
    verify.reset();
    runReplay.reset();
    exportPkg.reset();
    onClose();
  }

  return (
    <SidePanel isOpen={receipt !== null} onOpenChange={(o) => !o && close()} title="Decision receipt" subtitle={receipt?.action}>
      {receipt ? (
        <div className="space-y-5">
          <KeyValues
            columns={2}
            items={[
              ["Outcome", <StatusBadge key="o" state={receipt.outcome} kind="Decision outcome" />],
              ["Evidence class", <EvidenceChip key="e" evidenceClass={receipt.evidence_class} />],
              ["Reason codes", <span key="r" className="font-mono text-xs">{receipt.reason_codes.join(", ")}</span>],
              ["Policy release", <span key="p" className="font-mono text-2xs">{receipt.policy_release_id ?? "—"}</span>],
              ["Receipt hash", <CopyId key="h" value={receipt.receipt_hash} label="receipt hash" />],
              ["Receipt ID", <CopyId key="i" value={receipt.receipt_id} label="receipt ID" />],
            ]}
          />

          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Integrity</h3>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" icon={<FileCheck2 aria-hidden className="size-4" />} pending={verify.isPending} onPress={() => verify.mutate(id)}>
                Verify hash, ledger and checkpoint
              </Button>
              <Button size="sm" variant="secondary" pending={runReplay.isPending} onPress={() => runReplay.mutate(id)}>
                Replay decision
              </Button>
            </div>
            {verify.error ? <ApiErrorView error={verify.error} /> : null}
            {verification ? (
              <Banner
                tone={verification.ok ? "ok" : verification.ledgered && !verification.sealed ? "info" : "danger"}
                title={
                  verification.ok
                    ? "Verified: receipt, ledger entry, signed checkpoint and inclusion proof"
                    : !verification.ledgered
                      ? "Committed, not yet in the ledger"
                      : !verification.sealed
                        ? "In the ledger, not yet sealed by a checkpoint"
                        : "Verification failed"
                }
              >
                <ul className="mt-1 space-y-0.5 text-xs">
                  {verification.checks.map((c) => (
                    <li key={c.name}>
                      {c.ok ? "✓" : "✗"} {c.name}
                      {c.detail ? ` — ${c.detail}` : ""}
                    </li>
                  ))}
                </ul>
              </Banner>
            ) : null}
            {runReplay.error ? <ApiErrorView error={runReplay.error} /> : null}
            {replay ? (
              <Banner tone={REPLAY_TONE[replay.result] ?? "info"} title={replay.result.replaceAll("_", " ").toLowerCase()}>
                {replay.detail ?? (replay.differences?.length ? `Differs in: ${replay.differences.join(", ")}` : "Replayed against the historic policy release.")}
              </Banner>
            ) : null}
          </section>

          <section className="space-y-3">
            <h3 className="text-sm font-semibold">Export a signed package</h3>
            <Select label="Mode" value={exportMode} onChange={setExportMode} options={EXPORT_MODES} />
            <TextField label="Purpose" value={purpose} onChange={setPurpose} isRequired placeholder="Customer audit request" />
            <TextField label="Case reference" value={caseRef} onChange={setCaseRef} isRequired mono />
            <Button
              size="sm"
              pending={exportPkg.isPending}
              isDisabled={purpose.trim().length < 3 || !caseRef.trim()}
              onPress={() => exportPkg.mutate(id)}
            >
              Export (R3, recorded in chain of custody)
            </Button>
            {exportPkg.error ? <ApiErrorView error={exportPkg.error} /> : null}
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-semibold">Receipt</h3>
            <QueryView query={detail}>
              {(d) => (
                <>
                  <JsonView value={d.receipt} label="Canonical receipt" />
                  {d.annotations.length ? <JsonView value={d.annotations} label="Annotations" /> : null}
                </>
              )}
            </QueryView>
          </section>
        </div>
      ) : null}
    </SidePanel>
  );
}
