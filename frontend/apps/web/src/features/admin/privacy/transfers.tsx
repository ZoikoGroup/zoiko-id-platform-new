"use client";

import type { SubprocessorChange, SubprocessorChangeKind, TransferLedgerEntry, TransferIn } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { ArrowLeftRight, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PRIVACY_KEY } from "./shared";

/** Provider, data and transfer ledger (ZID-DTV-WF-04 B04; ZID-PRI-004 sections 08, 16, 19; CR-110). Default deny: a transfer to a processor is
 * allowed only for an approved processor that covers the data, in an approved location, with a lawful mechanism when it crosses a border, and with
 * no subprocessor or location change waiting; remote support access from abroad is a transfer too. A contract alone is not a transfer
 * authorization. Each check is recorded in the ledger with its reasons. */

const LEDGER_KEY = [...PRIVACY_KEY, "transfers"];
const CHANGES_KEY = [...PRIVACY_KEY, "changes"];
const CATEGORIES = ["D0", "D1", "D2", "D3", "D4"] as const;
const KINDS: { id: SubprocessorChangeKind; label: string }[] = [
  { id: "SUBPROCESSOR_ADDED", label: "A subprocessor was added" },
  { id: "SUBPROCESSOR_REMOVED", label: "A subprocessor was removed" },
  { id: "LOCATION_ADDED", label: "A processing location was added" },
  { id: "LOCATION_REMOVED", label: "A processing location was removed" },
];

export const TRANSFER_REASON_TEXT: Record<string, string> = {
  PROCESSOR_NOT_FOUND: "This processor is not registered.",
  PROCESSOR_NOT_APPROVED: "This processor is not approved.",
  DATA_CATEGORY_NOT_COVERED: "This processor is not approved for this kind of data.",
  SUBPROCESSOR_CHANGE_PENDING: "A subprocessor or location change is waiting for a decision: egress is blocked until then.",
  DESTINATION_NOT_AN_APPROVED_LOCATION: "The destination is not one of the processor's approved locations.",
  NO_TRANSFER_MECHANISM_RECORDED: "Data crosses a border and no lawful transfer mechanism is recorded.",
  MECHANISM_NOT_SUPPORTED_BY_THE_PACK: "The recorded mechanism is not one the jurisdiction pack supports.",
  TRANSFER_PERMITTED_BY_RECORDED_FACTS: "Permitted by the recorded facts.",
};

function reasonText(r: string): string {
  const m = /^REMOTE_ACCESS_FROM_([A-Z]{2})_NOT_APPROVED$/.exec(r);
  if (m) return `Support access from ${m[1]} is not an approved location.`;
  return TRANSFER_REASON_TEXT[r] ?? r;
}

const countries = (v: string) =>
  v
    .split(/[\s,]+/)
    .map((c) => c.trim().toUpperCase())
    .filter(Boolean);

/* ----------------------------------------------------------------------------------------------------------------------------- check */

function CheckTransfer() {
  const [f, setF] = useState({ processor_id: "", purpose_code: "IDENTITY_VERIFICATION", data_category: "D3", origin: "GB", destination: "", remote: "" });
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const body: TransferIn = {
    processor_id: f.processor_id.trim(),
    purpose_code: f.purpose_code.trim().toUpperCase(),
    data_category: (CATEGORIES.find((c) => c === f.data_category) ?? "D3") as TransferIn["data_category"],
    origin_country: f.origin.trim().toUpperCase(),
    destination_country: f.destination.trim().toUpperCase(),
    remote_access_countries: countries(f.remote),
  };
  const check = useCellMutation((api) => api.privacy.v2.checkTransfer(body), { invalidate: [LEDGER_KEY] });
  const ok = body.processor_id && /^[A-Z]{2}$/.test(body.origin_country) && /^[A-Z]{2}$/.test(body.destination_country);
  const result = check.data as { allowed?: boolean; reason_codes?: string[]; mechanism?: string | null } | undefined;
  return (
    <Card title="Check a transfer" description="Decides from the recorded facts and records the decision. It never sends any data.">
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField label="Processor ID" value={f.processor_id} onChange={set("processor_id")} mono isRequired />
        <TextField label="Purpose" value={f.purpose_code} onChange={set("purpose_code")} mono />
        <Select label="Data category" value={f.data_category} onChange={set("data_category")} options={CATEGORIES.map((c) => ({ id: c, label: c === "D3" ? "D3 (biometric, special category)" : c }))} />
        <TextField label="From (country)" value={f.origin} onChange={set("origin")} isRequired />
        <TextField label="To (country)" value={f.destination} onChange={set("destination")} isRequired />
        <TextField label="Support access from" description="countries, comma separated" value={f.remote} onChange={set("remote")} />
      </div>
      <Button className="mt-3" size="sm" variant="secondary" isDisabled={!ok} pending={check.isPending} onPress={() => check.mutate(undefined)}>
        Check
      </Button>
      {check.error ? <ApiErrorView error={check.error} /> : null}
      {result ? (
        <Banner className="mt-3" tone={result.allowed ? "ok" : "danger"} title={result.allowed ? `Allowed${result.mechanism ? ` (${result.mechanism})` : ""}` : "Refused"}>
          <ul className="list-disc pl-5">
            {(result.reason_codes ?? []).map((r) => (
              <li key={r}>{reasonText(r)}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
    </Card>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- changes */

function NotifyDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [processor, setProcessor] = useState("");
  const [kind, setKind] = useState<SubprocessorChangeKind>("SUBPROCESSOR_ADDED");
  const [detail, setDetail] = useState("");
  const [at, setAt] = useState("");
  const notify = useCellMutation(
    (api) => api.privacy.v2.notifyChange({ processor_id: processor.trim(), kind, detail: detail.trim(), notified_at: new Date(at).toISOString() }),
    {
      invalidate: [CHANGES_KEY],
      success: "Recorded: egress to this processor is blocked until someone decides",
      onSuccess: () => {
        setProcessor("");
        setDetail("");
        setAt("");
        onClose();
      },
    },
  );
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Record a processor change"
      description="What the processor told you. Until a different person decides, no data goes to that processor."
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button pending={notify.isPending} isDisabled={!processor.trim() || detail.trim().length < 2 || !at} onPress={() => notify.mutate(undefined)}>
            Record
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Processor ID" value={processor} onChange={setProcessor} mono isRequired />
        <Select label="Change" value={kind} onChange={(v) => setKind((KINDS.find((k) => k.id === v)?.id ?? "SUBPROCESSOR_ADDED") as SubprocessorChangeKind)} options={KINDS} />
        <TextField label="Detail" description="which subprocessor or location" value={detail} onChange={setDetail} isRequired />
        <TextField label="Notified at" type="datetime-local" value={at} onChange={setAt} isRequired />
        {notify.error ? <ApiErrorView error={notify.error} /> : null}
      </div>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------------------------------------------------------- page */

export function TransfersSection() {
  const ledger = useCellQuery(LEDGER_KEY, (api) => api.privacy.v2.transfers());
  const [pendingOnly, setPendingOnly] = useState(true);
  const changes = useCellQuery([...CHANGES_KEY, pendingOnly], (api) => api.privacy.v2.changes(pendingOnly));
  const [notifying, setNotifying] = useState(false);
  const decide = useCellMutation((api, v: { c: SubprocessorChange; approve: boolean }) => api.privacy.v2.decideChange(v.c.change_id, v.approve), {
    invalidate: [CHANGES_KEY],
    success: (r) => ((r as { state?: string }).state === "APPROVED" ? "Change approved" : "Change decided"),
  });
  const ledgerCols: Column<TransferLedgerEntry>[] = [
    { key: "when", header: "Checked", pinned: true, render: (d) => <DateTime value={d.evaluated_at} /> },
    { key: "processor", header: "Processor", render: (d) => <span className="font-mono text-xs">{d.processor_id}</span> },
    { key: "route", header: "Route", render: (d) => `${d.origin_country} → ${d.destination_country}${d.remote_access_countries.length ? ` (+ access from ${d.remote_access_countries.join(", ")})` : ""}` },
    { key: "data", header: "Data", render: (d) => <span className="font-mono text-xs">{`${d.data_category} · ${d.purpose_code}`}</span> },
    { key: "result", header: "Result", render: (d) => <Badge tone={d.allowed ? "ok" : "danger"}>{d.allowed ? "Allowed" : "Refused"}</Badge> },
    { key: "why", header: "Why", render: (d) => <span className="text-xs">{d.reason_codes.map(reasonText).join(" ")}</span> },
  ];
  const changeCols: Column<SubprocessorChange>[] = [
    { key: "when", header: "Notified", pinned: true, render: (c) => <DateTime value={c.notified_at} /> },
    { key: "processor", header: "Processor", render: (c) => <span className="font-mono text-xs">{c.processor_id}</span> },
    { key: "kind", header: "Change", render: (c) => KINDS.find((k) => k.id === c.kind)?.label ?? c.kind },
    { key: "detail", header: "Detail", render: (c) => <span className="text-xs">{c.detail}</span> },
    { key: "state", header: "State", render: (c) => <StatusBadge state={c.state === "PENDING_APPROVAL" ? "PENDING" : c.state} kind="Change" /> },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (c) =>
        c.state === "PENDING_APPROVAL" ? (
          <span className="inline-flex gap-1.5">
            <Button size="xs" pending={decide.isPending} onPress={() => decide.mutate({ c, approve: true })} aria-label={`Approve change ${c.detail}`}>
              Approve
            </Button>
            <Button size="xs" variant="secondary" pending={decide.isPending} onPress={() => decide.mutate({ c, approve: false })} aria-label={`Reject change ${c.detail}`}>
              Reject
            </Button>
          </span>
        ) : null,
    },
  ];
  return (
    <div className="space-y-5">
      <CheckTransfer />
      <Card
        padded={false}
        title="Subprocessor and location changes"
        description="A change waiting for a decision blocks every transfer to that processor. The person who recorded it cannot decide it."
        actions={
          <span className="flex items-center gap-3">
            <Checkbox isSelected={pendingOnly} onChange={setPendingOnly}>
              Waiting only
            </Checkbox>
            <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setNotifying(true)}>
              Record a change
            </Button>
          </span>
        }
      >
        <QueryView query={changes}>
          {(d) => <DataTable caption="Processor changes" columns={changeCols} rows={d.items} rowKey={(c) => c.change_id} empty={<EmptyState icon={ArrowLeftRight} title="No changes waiting" />} />}
        </QueryView>
        {decide.error ? <ApiErrorView error={decide.error} title="Not decided" /> : null}
      </Card>
      <Card padded={false} title="Transfer ledger" description="Every transfer check, with its reasons. A contract is necessary but never by itself a transfer authorization.">
        <QueryView query={ledger}>
          {(d) => <DataTable caption="Transfer ledger" columns={ledgerCols} rows={d.items} rowKey={(x) => x.decision_id} empty={<EmptyState icon={ArrowLeftRight} title="No transfers checked yet" />} />}
        </QueryView>
      </Card>
      <NotifyDialog isOpen={notifying} onClose={() => setNotifying(false)} />
    </div>
  );
}
