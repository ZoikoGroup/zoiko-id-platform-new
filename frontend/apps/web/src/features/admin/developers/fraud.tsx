"use client";

import type { FraudAction, FraudLead } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ACTION_STATE, actionButtons, coverageText, FINDING, LEAD_STATE, leadActions, riskTone } from "./fraud-logic";

const KEY = ["admin", "developers", "verify-api", "fraud"];
const FINDINGS = [
  { id: "CONFIRMED", label: "Confirmed" },
  { id: "NO_FINDING", label: "Nothing found" },
  { id: "INCONCLUSIVE", label: "Inconclusive" },
];

function Overview() {
  const q = useCellQuery([...KEY, "overview"], (api) => api.verifyApi.fraudOverview());
  const refresh = useCellMutation<void, unknown>((api) => api.verifyApi.fraudRefresh(), { invalidate: [KEY as never], success: "Read the latest sessions in" });
  return (
    <QueryView query={q}>
      {(o) => (
        <Card
          title="Fraud signals"
          description="Alerts, investigations and confirmed findings are different numbers. A lead is a place to look, never a finding."
          actions={<Button variant="secondary" pending={refresh.isPending} onPress={() => refresh.mutate()}>Read in new sessions</Button>}
        >
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
            {[["Leads raised", o.leads.raised], ["Being investigated", o.leads.under_investigation], ["Confirmed after review", o.leads.confirmed_findings], ["Cleared", o.leads.cleared], ["No outcome yet", o.leads.no_outcome_yet]].map(([k, v]) => (
              <div key={String(k)}><dt className="text-ink-muted">{k}</dt><dd className="text-lg font-semibold text-ink">{v}</dd></div>
            ))}
          </dl>
          <p className="mt-3 text-sm text-ink-muted">Links: {o.links.active} active, {o.links.excluded} excluded, {o.links.retracted} retracted. A link is a reason to look, not proof of fraud.</p>
          <Banner tone={o.coverage.sessions_with_observation === 0 && o.coverage.sessions > 0 ? "warn" : "info"} title="Coverage">{coverageText(o.coverage)}</Banner>
        </Card>
      )}
    </QueryView>
  );
}

function LeadDetail({ lead, onDone }: { lead: FraudLead; onDone: () => void }) {
  const can = leadActions(lead);
  const [note, setNote] = useState("");
  const [finding, setFinding] = useState("NO_FINDING");
  const done = { invalidate: [KEY as never], onSuccess: onDone };
  const triage = useCellMutation((api, s: string) => api.verifyApi.fraudTriage(lead.lead_id, s, lead.lead_version, note || undefined), { ...done, success: "Recorded" });
  const escalate = useCellMutation<void, unknown>((api) => api.verifyApi.fraudEscalate(lead.lead_id, lead.lead_version), { ...done, success: "Sent for review: a different person decides" });
  const record = useCellMutation((api, f: string) => api.verifyApi.fraudFinding(lead.lead_id, f, lead.lead_version, note || undefined), { ...done, success: "Finding recorded" });
  const addNote = useCellMutation((api, b: string) => api.verifyApi.fraudNote(lead.lead_id, b), { ...done, success: "Note added" });
  return (
    <Card title={`Lead ${lead.lead_id}`} description={`${lead.summary} Rule ${lead.rule_id} version ${lead.rule_version}. Looks at ${lead.sessions} session(s).`}>
      <div className="space-y-3 text-sm">
        {lead.uncertain_facts.length ? <Banner tone="warn" title="What is uncertain"><ul className="list-disc pl-5">{lead.uncertain_facts.map((f) => <li key={f}>{f}</li>)}</ul></Banner> : null}
        {lead.snapshot_current === false ? <Banner tone="warn" title="The graph changed since this lead was raised">Look again before you decide.</Banner> : null}
        <TextField label="Note or reason" multiline rows={2} value={note} onChange={setNote} />
        <div className="flex flex-wrap gap-2">
          {can.analyze ? <Button variant="secondary" onPress={() => triage.mutate("IN_ANALYSIS")}>Start looking</Button> : null}
          {can.clear ? <Button variant="secondary" isDisabled={note.trim().length < 3} onPress={() => triage.mutate("CLEARED")}>Clear with a reason</Button> : null}
          {can.escalate ? <Button onPress={() => escalate.mutate()}>Send for review</Button> : null}
          <Button variant="secondary" isDisabled={note.trim().length < 3} onPress={() => addNote.mutate(note)}>Add note</Button>
        </div>
        {can.finding ? (
          <div className="flex flex-wrap items-end gap-2">
            <Select label="Finding after review" options={FINDINGS} value={finding} onChange={setFinding} description="A different person from whoever reviewed the case records this." />
            <Button onPress={() => record.mutate(finding)}>Record finding</Button>
          </div>
        ) : null}
        {[triage, escalate, record, addNote].map((m, i) => (m.error ? <Banner key={i} tone="danger" title="Not done">{String((m.error as Error).message ?? m.error)}</Banner> : null))}
      </div>
    </Card>
  );
}

function Leads() {
  const q = useCellQuery([...KEY, "leads"], (api) => api.verifyApi.fraudLeads());
  const [open, setOpen] = useState<FraudLead | null>(null);
  const cols: Column<FraudLead>[] = [
    { key: "id", header: "Lead", pinned: true, render: (l) => <span className="font-mono text-xs">{l.lead_id}</span>, sortValue: (l) => l.lead_id },
    { key: "risk", header: "Risk", render: (l) => <Badge tone={riskTone(l.risk_class)}>{l.risk_class.toLowerCase()}</Badge> },
    { key: "st", header: "State", render: (l) => <Badge tone={(LEAD_STATE[l.status] ?? { tone: "neutral" as const }).tone}>{(LEAD_STATE[l.status] ?? { label: l.status }).label}</Badge> },
    { key: "f", header: "Finding", render: (l) => FINDING[l.finding] ?? l.finding },
    { key: "s", header: "Sessions", render: (l) => l.sessions },
    { key: "sum", header: "Why", render: (l) => l.summary },
  ];
  return (
    <div className="space-y-3">
      <QueryView query={q}>{(d) => <DataTable caption="Fraud leads, newest first" columns={cols} rows={d.items} rowKey={(l) => l.lead_id} onRowAction={setOpen} selectedKey={open?.lead_id ?? null} empty="No leads. That means nothing matched a rule, not that nothing is wrong." />}</QueryView>
      {open ? <LeadDetail lead={open} onDone={() => setOpen(null)} /> : null}
    </div>
  );
}

function Proposals({ me }: { me: string | null }) {
  const q = useCellQuery([...KEY, "actions"], (api) => api.verifyApi.fraudActions());
  const [note, setNote] = useState("");
  const decide = useCellMutation((api, v: { a: FraudAction; op: "approve" | "reject" | "revoke" }) => api.verifyApi.fraudActionDecide(v.a.action_id, v.op, { manifest_checksum: v.a.manifest_checksum, note: note || undefined }), { invalidate: [KEY as never], success: "Recorded" });
  return (
    <div className="space-y-3">
      <Banner tone="info" title="A proposal changes nothing">An approved proposal is a signed instruction for your own people and systems. It never blocks a session or a customer by itself, and it needs a second person.</Banner>
      <TextField label="Note" value={note} onChange={setNote} />
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Action proposals"
            columns={[
              { key: "t", header: "Proposal", render: (a) => a.proposal_type.replaceAll("_", " ").toLowerCase() },
              { key: "n", header: "Targets", render: (a) => a.targets.length },
              { key: "st", header: "State", render: (a) => <Badge tone={(ACTION_STATE[a.status] ?? { tone: "neutral" as const }).tone}>{(ACTION_STATE[a.status] ?? { label: a.status }).label}</Badge> },
              { key: "b", header: "Basis", render: (a) => a.basis_ref },
              {
                key: "act", header: "", render: (a) => {
                  const b = actionButtons(a, me);
                  return (
                    <div className="flex gap-1">
                      {b.approve ? <Button variant="secondary" onPress={() => decide.mutate({ a, op: "approve" })}>Approve</Button> : null}
                      {b.reject ? <Button variant="secondary" isDisabled={note.trim().length < 3} onPress={() => decide.mutate({ a, op: "reject" })}>Reject</Button> : null}
                      {b.revoke ? <Button variant="secondary" isDisabled={note.trim().length < 3} onPress={() => decide.mutate({ a, op: "revoke" })}>Revoke</Button> : null}
                    </div>
                  );
                },
              },
            ]}
            rows={d.items}
            rowKey={(a) => a.action_id}
            empty="No proposals."
          />
        )}
      </QueryView>
      {decide.error ? <Banner tone="danger" title="Not done">{String((decide.error as Error).message ?? decide.error)}</Banner> : null}
    </div>
  );
}

/** The fraud graph for the company's own analysts (CR-168). Four separate duties are enforced by the cell: read, analyze, propose, approve. */
export function FraudCard({ me = null }: { me?: string | null }) {
  return (
    <div className="space-y-4">
      <Overview />
      <Leads />
      <Proposals me={me} />
    </div>
  );
}
