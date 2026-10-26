"use client";

import type { CustodyRecord, EvidenceCase, EvidenceCaseKind } from "@zoiko/api-client";
import { Button, Card, Code, DataTable, DateTime, Dialog, EmptyState, KeyValues, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { FolderLock, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["evidence", "cases"];
const KINDS: { id: EvidenceCaseKind; label: string }[] = [
  { id: "INVESTIGATION", label: "Investigation" },
  { id: "AUDIT", label: "Audit" },
  { id: "LEGAL", label: "Legal" },
  { id: "INCIDENT", label: "Incident" },
  { id: "DATA_RIGHTS", label: "Data-rights request" },
];

function inDays(n: number): string {
  const d = new Date(Date.now() + n * 86400_000);
  return d.toISOString().slice(0, 10);
}

function OpenCase({ onClose, onOpened }: { onClose: () => void; onOpened: (id: string) => void }) {
  const [kind, setKind] = useState<EvidenceCaseKind>("INVESTIGATION");
  const [purpose, setPurpose] = useState("");
  const [reference, setReference] = useState("");
  const [basis, setBasis] = useState("");
  const [expires, setExpires] = useState(inDays(90));
  const open = useCellMutation(
    (api) =>
      api.evidence.openCase({
        kind,
        purpose: purpose.trim(),
        reference: reference.trim(),
        legal_basis: basis.trim() || null,
        expires_at: new Date(`${expires}T23:59:59Z`).toISOString(),
      }),
    { invalidate: [KEY], success: "Case opened", onSuccess: (r) => onOpened(r.case_id) },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !open.isPending && onClose()}
      title="Open an evidence case"
      description="Evidence is collected into a case for a stated purpose; every item gets a chain-of-custody record."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={open.isPending} isDisabled={purpose.trim().length < 3 || !reference.trim()} onPress={() => open.mutate(undefined)}>
            Open case
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Kind" value={kind} onChange={(v) => setKind(v as EvidenceCaseKind)} options={KINDS} />
          <TextField label="Reference" isRequired mono value={reference} onChange={setReference} placeholder="INC-2026-17" />
        </div>
        <TextField label="Purpose" isRequired value={purpose} onChange={setPurpose} description="Why evidence is being gathered. Kept with every custody record." />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Legal basis (optional)" value={basis} onChange={setBasis} />
          <TextField label="Case expires" type="date" isRequired value={expires} onChange={setExpires} />
        </div>
        {open.error ? <ApiErrorView error={open.error} title="The case was not opened" /> : null}
      </div>
    </Dialog>
  );
}

function CaseDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const key = [...KEY, id];
  const c = useCellQuery(key, (api) => api.evidence.case(id));
  const [item, setItem] = useState("");
  const [purpose, setPurpose] = useState("");
  const add = useCellMutation((api) => api.evidence.addCaseItem(id, { evidence_item_id: item.trim(), purpose: purpose.trim() }), {
    invalidate: [key],
    success: "Item collected",
    onSuccess: () => setItem(""),
  });
  const close = useCellMutation((api) => api.evidence.closeCase(id), { invalidate: [KEY, key], success: "Case closed" });
  const columns: Column<CustodyRecord>[] = [
    { key: "item", header: "Evidence item", render: (r) => <Code>{r.evidence_item_id}</Code> },
    { key: "action", header: "Custody", render: (r) => <StatusBadge state={r.custody_action} kind="Custody" size="xs" /> },
    { key: "purpose", header: "Purpose", render: (r) => r.purpose },
    { key: "at", header: "When", render: (r) => <DateTime value={r.created_at} /> },
  ];
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Evidence case" size="lg" footer={<Button onPress={onClose}>Close</Button>}>
      <QueryView query={c}>
        {(d) => (
          <div className="space-y-4">
            <KeyValues
              columns={3}
              items={[
                ["Kind", d.case_kind.toLowerCase().replace(/_/g, " ")],
                ["Status", <StatusBadge key="s" state={d.status} kind="Case" size="xs" />],
                ["Expires", <DateTime key="e" value={d.expires_at} />],
                ["Purpose", d.purpose],
                ["Legal basis", d.legal_basis ?? "—"],
                ["Opened", <DateTime key="o" value={d.opened_at} />],
              ]}
            />
            <Card title="Chain of custody" padded={false}>
              {d.items.length ? (
                <DataTable caption="Custody records" columns={columns} rows={d.items} rowKey={(r) => r.custody_id} />
              ) : (
                <EmptyState title="No items yet" compact>
                  Collect receipts, audit records or artifacts by their ID.
                </EmptyState>
              )}
            </Card>
            {d.status === "OPEN" ? (
              <div className="space-y-3 rounded-card border border-border p-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <TextField label="Evidence item ID" mono value={item} onChange={setItem} description="A receipt, audit record or artifact ID." />
                  <TextField label="Purpose" value={purpose} onChange={setPurpose} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" pending={add.isPending} isDisabled={!item.trim() || purpose.trim().length < 3} onPress={() => add.mutate(undefined)}>
                    Collect item
                  </Button>
                  <Button size="sm" variant="secondary" pending={close.isPending} onPress={() => close.mutate(undefined)}>
                    Close case
                  </Button>
                </div>
                {add.error ? <ApiErrorView error={add.error} /> : null}
                {close.error ? <ApiErrorView error={close.error} /> : null}
              </div>
            ) : null}
          </div>
        )}
      </QueryView>
    </Dialog>
  );
}

/** Evidence cases and chain of custody (EVIDENCE-001 §21). */
export function CasesSection() {
  const [status, setStatus] = useState("OPEN");
  const cases = useCellQuery([...KEY, status], (api) => api.evidence.cases(status === "ALL" ? undefined : status));
  const [opening, setOpening] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<EvidenceCase>[] = [
    { key: "kind", header: "Kind", render: (c) => c.case_kind.toLowerCase().replace(/_/g, " ") },
    { key: "purpose", header: "Purpose", pinned: true, render: (c) => <span className="text-ink">{c.purpose}</span> },
    { key: "status", header: "Status", render: (c) => <StatusBadge state={c.status} kind="Case" size="xs" /> },
    { key: "opened", header: "Opened", render: (c) => <DateTime value={c.opened_at} />, sortValue: (c) => c.opened_at },
    { key: "expires", header: "Expires", render: (c) => <DateTime value={c.expires_at} /> },
    {
      key: "a",
      header: "",
      align: "end",
      render: (c) => (
        <Button size="xs" variant="secondary" onPress={() => setOpen(c.case_id)}>
          Open
        </Button>
      ),
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <Select
          label="Show"
          value={status}
          onChange={setStatus}
          options={[
            { id: "OPEN", label: "Open cases" },
            { id: "CLOSED", label: "Closed cases" },
            { id: "ALL", label: "All cases" },
          ]}
        />
        <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setOpening(true)}>
          Open case
        </Button>
      </div>
      <Card padded={false}>
        <QueryView query={cases}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Evidence cases" columns={columns} rows={r.items} rowKey={(c) => c.case_id} />
            ) : (
              <EmptyState title="No cases" icon={FolderLock}>
                Open a case to collect evidence for an investigation, audit, legal matter, incident or data-rights request.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {opening ? (
        <OpenCase
          onClose={() => setOpening(false)}
          onOpened={(id) => {
            setOpening(false);
            setOpen(id);
          }}
        />
      ) : null}
      {open ? <CaseDetail id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}
