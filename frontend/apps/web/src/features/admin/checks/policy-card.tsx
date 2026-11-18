"use client";

import { CHECK_METHODS, type CheckMethod, type CheckPolicy } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, EmptyState, TextField, type Column, type Tone } from "@zoiko/ui";
import { FileCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

/** A company's verification policy (ZID-DTV-WF-05 A11; CR-118): the checks every case must ask for, and the proof of address documents accepted, per
 * country and purpose. A draft is submitted by its author and approved by someone else; only an approved policy has effect, and it can only narrow
 * what the platform accepts. Requiring a check never switches it on. */

const KEY = ["checks", "policies"] as const;
const TONE: Record<CheckPolicy["status"], Tone> = { DRAFT: "neutral", SUBMITTED: "warn", APPROVED: "ok", REJECTED: "danger", SUPERSEDED: "neutral" };

/** "UTILITY_BILL=90, BANK_STATEMENT=60" -> { UTILITY_BILL: 90, BANK_STATEMENT: 60 }; null when it does not read. */
export function parseTypes(text: string): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const part of text.split(",").map((s) => s.trim()).filter(Boolean)) {
    const m = /^([A-Z][A-Z_]{2,40})\s*=\s*(\d{1,4})$/.exec(part.toUpperCase());
    if (!m) return null;
    out[m[1]!] = Number(m[2]);
  }
  return out;
}

export function PolicyCard() {
  const q = useCellQuery([...KEY], (api) => api.checks.policies());
  const [market, setMarket] = useState("GB");
  const [purpose, setPurpose] = useState("ONBOARDING");
  const [required, setRequired] = useState<CheckMethod[]>([]);
  const [types, setTypes] = useState("UTILITY_BILL=90");
  const [reason, setReason] = useState("");
  const parsed = parseTypes(types);
  const inv = { invalidate: [[...KEY]] };
  const draft = useCellMutation(
    (api) => api.checks.draftPolicy({ market: market.trim().toUpperCase(), purpose: purpose.trim(), required_methods: required, document_types: parsed ?? {} }),
    { ...inv, success: "Draft saved: submit it, then someone else approves it" },
  );
  const submit = useCellMutation((api, id: string) => api.checks.submitPolicy(id), { ...inv, success: "Submitted for approval" });
  const decide = useCellMutation((api, v: { id: string; approve: boolean }) => api.checks.decidePolicy(v.id, v.approve, reason.trim()), {
    ...inv,
    success: (p) => (p.status === "APPROVED" ? "Approved: it applies to new cases now" : "Rejected"),
  });
  const toggle = (m: CheckMethod) => setRequired((p) => (p.includes(m) ? p.filter((x) => x !== m) : [...p, m]));
  const cols: Column<CheckPolicy>[] = [
    { key: "where", header: "Country · purpose", render: (p) => `${p.market} · ${p.purpose}` },
    { key: "status", header: "Status", render: (p) => <Badge tone={TONE[p.status]}>{p.status}</Badge> },
    { key: "required", header: "Required checks", render: (p) => p.required_methods.join(", ") || "-" },
    { key: "docs", header: "Documents (max days)", render: (p) => Object.entries(p.document_types).map(([k, v]) => `${k} ${v}`).join(", ") || "platform default" },
    {
      key: "act",
      header: "",
      align: "end",
      render: (p) =>
        p.status === "DRAFT" ? (
          <Button size="xs" variant="secondary" onPress={() => submit.mutate(p.policy_id)}>Submit</Button>
        ) : p.status === "SUBMITTED" ? (
          <span className="inline-flex gap-1.5">
            <Button size="xs" onPress={() => decide.mutate({ id: p.policy_id, approve: true })} isDisabled={reason.trim().length < 10}>Approve</Button>
            <Button size="xs" variant="secondary" onPress={() => decide.mutate({ id: p.policy_id, approve: false })} isDisabled={reason.trim().length < 10}>Reject</Button>
          </span>
        ) : null,
    },
  ];
  return (
    <Card title="Verification policy (A11)" description="What every case must ask for, and which proof of address documents you accept, per country and purpose. It takes effect only when someone other than its author approves it, and can only narrow what the platform accepts.">
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField label="Country (ISO)" value={market} onChange={setMarket} isRequired />
        <TextField label="Purpose" value={purpose} onChange={setPurpose} isRequired />
        <TextField
          label="Accepted documents"
          value={types}
          onChange={setTypes}
          description="TYPE=max days, comma separated; empty for the platform default"
          errorMessage={parsed ? undefined : "Write TYPE=days, e.g. UTILITY_BILL=90"}
        />
      </div>
      <p className="mt-3 text-sm font-medium text-ink">Required checks</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {CHECK_METHODS.map((m) => (
          <Button key={m} size="xs" variant={required.includes(m) ? "primary" : "secondary"} onPress={() => toggle(m)} aria-pressed={required.includes(m)}>
            {m}
          </Button>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <Button onPress={() => draft.mutate(undefined)} pending={draft.isPending} isDisabled={!parsed || market.trim().length !== 2 || !purpose.trim()}>
          Save draft
        </Button>
      </div>
      <TextField label="Reason for an approval or rejection" value={reason} onChange={setReason} description="at least 10 characters; you cannot decide your own policy" />
      {[draft.error, submit.error, decide.error].map((e, i) => (e ? <ApiErrorView key={i} error={e} /> : null))}
      <QueryView query={q}>
        {(d) => <DataTable caption="Verification policies" columns={cols} rows={d.items} rowKey={(p) => p.policy_id} empty={<EmptyState icon={FileCheck} title="No policy yet: the platform defaults apply" />} />}
      </QueryView>
    </Card>
  );
}
