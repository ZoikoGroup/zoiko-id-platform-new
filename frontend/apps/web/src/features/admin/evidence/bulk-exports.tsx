"use client";

import type { BulkExport } from "@zoiko/api-client";
import { useRights } from "@/lib/rights";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Download, PackagePlus, Package } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { EXPIRIES, MAX_RECEIPTS, parseReceiptIds, timeLeft } from "./bulk-exports-logic";
import { EXPORT_MODES } from "./receipts";

const BULK_KEY = ["admin", "evidence", "bulk-exports"];

function save(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name.replace(/[^A-Za-z0-9._-]/g, "-");
  a.click();
  URL.revokeObjectURL(url);
}

function NewExportDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState("CUSTOMER_VERIFY");
  const [purpose, setPurpose] = useState("");
  const [caseRef, setCaseRef] = useState("");
  const [expiry, setExpiry] = useState("1440");
  const parsed = parseReceiptIds(text);

  const create = useCellMutation(
    (api) => api.evidence.createBulkExport({ receipt_ids: parsed.ids, mode, purpose: purpose.trim(), case_ref: caseRef.trim(), ttl_minutes: Number(expiry) }),
    {
      invalidate: [BULK_KEY],
      success: "Bulk export created",
      onSuccess: (pkg) => {
        save(`evidence-bulk-${String((pkg as { export_id?: string }).export_id ?? "export")}.json`, pkg);
        close(true);
      },
    },
  );

  function close(force = false) {
    if (create.isPending && !force) return;
    setText("");
    setPurpose("");
    setCaseRef("");
    setMode("CUSTOMER_VERIFY");
    setExpiry("1440");
    create.reset();
    onClose();
  }

  const valid = parsed.ids.length > 0 && parsed.invalid.length === 0 && !parsed.tooMany && purpose.trim().length >= 3 && caseRef.trim().length > 0;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="New bulk export"
      description="Several receipts in one signed package that stops being downloadable when it expires."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={() => close()}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Create and download
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <Banner tone="info" title="This asks you to sign in again">
          A bulk export is a critical action, so you are asked to confirm with your passkey. Every receipt in it also leaves its own custody record.
        </Banner>
        <TextField
          label="Receipt IDs"
          isRequired
          multiline
          rows={6}
          mono
          value={text}
          onChange={setText}
          placeholder={"one receipt ID per line"}
          description={`${parsed.ids.length} receipt${parsed.ids.length === 1 ? "" : "s"} (up to ${MAX_RECEIPTS}). Copy IDs from the Receipts tab.`}
          errorMessage={
            parsed.invalid.length
              ? `Not a receipt ID: ${parsed.invalid.slice(0, 3).join(", ")}${parsed.invalid.length > 3 ? "…" : ""}`
              : parsed.tooMany
                ? `Use at most ${MAX_RECEIPTS} receipts in one export.`
                : undefined
          }
        />
        <Select label="Mode" value={mode} onChange={setMode} options={EXPORT_MODES} />
        <TextField label="Purpose" isRequired value={purpose} onChange={setPurpose} placeholder="Customer audit request" />
        <TextField label="Case reference" isRequired value={caseRef} onChange={setCaseRef} placeholder="CASE-2026-014" />
        <Select label="Download window" value={expiry} onChange={setExpiry} options={EXPIRIES.map((e) => ({ id: e.id, label: e.label }))} />
        {create.error ? <ApiErrorView error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

export function BulkExportsSection() {
  const list = useCellQuery(BULK_KEY, (api) => api.evidence.bulkExports(50));
  const [creating, setCreating] = useState(false);
  const mayRequest = useRights().can("f.request-sensitive-evidence-export", "S", "X");
  const download = useCellMutation((api, id: string) => api.evidence.bulkExport(id), {
    invalidate: [BULK_KEY],
    onSuccess: (pkg, id) => save(`evidence-bulk-${id}.json`, pkg),
  });

  const columns: Column<BulkExport>[] = [
    { key: "purpose", header: "Purpose", pinned: true, render: (e) => <span className="font-medium">{e.purpose}</span>, sortValue: (e) => e.purpose },
    { key: "mode", header: "Mode", render: (e) => <span className="text-xs">{e.mode}</span> },
    { key: "count", header: "Receipts", align: "end", render: (e) => e.receipt_count, sortValue: (e) => e.receipt_count },
    { key: "status", header: "Status", render: (e) => <StatusBadge state={e.status} kind="Export status" /> },
    { key: "window", header: "Download window", render: (e) => (e.status === "AVAILABLE" ? timeLeft(e.expires_at) : "Erased") },
    { key: "downloads", header: "Downloads", align: "end", render: (e) => e.downloads },
    { key: "created", header: "Created", render: (e) => <DateTime value={e.created_at} />, sortValue: (e) => e.created_at },
    { key: "hash", header: "Package hash", defaultHidden: true, render: (e) => <CopyId value={e.package_hash} label="package hash" /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (e) =>
        e.status === "AVAILABLE" ? (
          <Button size="xs" variant="secondary" icon={<Download aria-hidden className="size-3.5" />} isDisabled={download.isPending} onPress={() => download.mutate(e.export_id)}>
            Download
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        title="Bulk exports"
        description="Signed packages of several receipts. Each stops being downloadable at its expiry and is then erased; the record of it stays."
        actions={
          mayRequest ? (
            <Button icon={<PackagePlus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
              New bulk export
            </Button>
          ) : undefined
        }
        padded={false}
      >
        <QueryView query={list}>
          {(data) => (
            <DataTable
              caption="Bulk exports"
              columns={columns}
              rows={data.items}
              rowKey={(e) => e.export_id}
              empty={
                <EmptyState icon={Package} title="No bulk exports yet">
                  Create one to hand an auditor or a regulator many receipts in a single signed package.
                </EmptyState>
              }
            />
          )}
        </QueryView>
        {download.error ? (
          <div className="p-4">
            <ApiErrorView error={download.error} />
          </div>
        ) : null}
      </Card>
      <NewExportDialog isOpen={creating} onClose={() => setCreating(false)} />
    </>
  );
}
