"use client";

import type { TrustEvidence } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, PageHeader, Tabs, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { RegError } from "../regulatory/common";

import { figure, hoursText } from "./logic";

const K = ["trust", "ops"] as const;

function Monitoring() {
  const q = useCellQuery([...K, "monitoring"], (api) => api.trustCenter.monitoring(), { refetchInterval: 60_000 });
  const [url, setUrl] = useState("");
  const [html, setHtml] = useState("");
  const scan = useCellMutation((api, v: { url: string; content: string }) => api.trustCenter.scan([v]));
  return (
    <div className="space-y-4">
      <QueryView query={q}>
        {(m) => (
          <>
            <Banner tone={m.release_gate.open ? "ok" : "danger"} title={m.release_gate.open ? "Release gate open: no unsupported live claim" : "Release gate closed"}>
              {m.release_gate.reasons.length ? <ul className="list-disc pl-5">{m.release_gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : "Every live claim has current evidence, and no obligation is overdue."}
            </Banner>
            <Card title="Right now">
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                {[["Live claims", m.counts.live_claims], ["Unsupported live", m.unsupported_live_claims], ["Waiting for approval", m.counts.pending_claims], ["Withdrawals awaiting review", m.counts.withdrawals_overdue_review],
                  ["Reports past first response", m.counts.reports_past_target], ["Figures not validated", m.counts.figures_unvalidated], ["Room requests open", m.counts.room_requests_open], ["Room downloads, 30 days", m.counts.room_downloads_30d]].map(([k, v]) => (
                  <div key={String(k)}><dt className="text-ink-muted">{k}</dt><dd className="text-lg font-semibold text-ink">{v}</dd></div>
                ))}
              </dl>
              <p className="mt-2 text-sm text-ink-muted">Median time from publication to withdrawal: {hoursText(m.median_hours_published_to_withdrawn)}.</p>
            </Card>
            <Card title="Support ending soon" description="Live claims whose own date, or whose evidence period, ends within 30 days.">
              {m.expiring.length === 0 ? <p className="text-sm text-ink-muted">Nothing is due in the next 30 days.</p> : (
                <ul className="list-disc pl-5 text-sm">{m.expiring.map((w, i) => <li key={i}>Claim {w.claim_id.slice(0, 8)}, revision {w.revision}: {w.reason} ({w.days_left} days)</li>)}</ul>
              )}
            </Card>
          </>
        )}
      </QueryView>
      <Card title="Scan a page for unapproved claims" description="Paste a page address and its HTML (the console never fetches addresses itself). Every claim-like sentence must match a live approved one.">
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); scan.mutate({ url, content: html }); }}>
          <TextField label="Page address" isRequired value={url} onChange={setUrl} />
          <TextField label="Page HTML or text" multiline rows={5} isRequired value={html} onChange={setHtml} />
          <Button type="submit" pending={scan.isPending}>Scan</Button>
        </form>
        {scan.error ? <RegError error={scan.error} /> : null}
        {scan.data ? (
          scan.data.clean ? <Banner tone="ok" title={`Clean: ${scan.data.claim_like_statements} claim-like sentence(s), all approved`} /> : (
            <ul className="mt-3 space-y-2 text-sm">{scan.data.findings.map((f, i) => <li key={i} className="rounded border border-line p-2"><Badge tone="danger">{f.code.replaceAll("_", " ").toLowerCase()}</Badge> {f.sentence}<span className="block text-ink-muted">{f.detail}</span></li>)}</ul>
          )
        ) : null}
      </Card>
    </div>
  );
}

function Evidence() {
  const q = useCellQuery([...K, "evidence"], (api) => api.trustCenter.evidence());
  const confirm = useCellMutation((api, id: string) => api.trustCenter.confirmEvidence(id), { invalidate: [K as never], success: "Confirmed with the issuer" });
  const lifecycle = useCellMutation((api, v: { id: string; l: string }) => api.trustCenter.evidenceLifecycle(v.id, v.l), { invalidate: [K as never], success: (r) => `Updated. ${r.claims_resting_on_it.length} claim(s) rest on it and are re-checked at once.` });
  const cols: Column<TrustEvidence>[] = [
    { key: "scheme", header: "Scheme", render: (e) => `${e.scheme} (${e.evidence_type.replaceAll("_", " ")})` },
    { key: "issuer", header: "Issuer", render: (e) => e.issuer },
    { key: "scope", header: "Covers", render: (e) => `${e.legal_entity}: ${e.products.join(", ")}${e.regions.length ? ` in ${e.regions.join(", ")}` : " (no region stated)"}` },
    { key: "period", header: "Period", render: (e) => `${e.period_start} to ${e.period_end}` },
    { key: "held", header: "Held by", render: (e) => (e.holder === "zoiko" ? "Zoiko iD" : `${e.holder} (supplier)`) },
    { key: "state", header: "State", render: (e) => <><Badge tone={e.lifecycle === "ACTIVE" ? "ok" : "danger"}>{e.lifecycle.toLowerCase()}</Badge> {e.issuer_confirmed ? "" : <Badge tone="warn">not confirmed</Badge>}</> },
    {
      key: "act", header: "", render: (e) => (
        <div className="flex gap-1">
          {!e.issuer_confirmed ? <Button variant="secondary" onPress={() => confirm.mutate(e.evidence_id)}>Confirm</Button> : null}
          {e.lifecycle === "ACTIVE" ? <Button variant="secondary" onPress={() => lifecycle.mutate({ id: e.evidence_id, l: "SUSPENDED" })}>Suspend</Button> : null}
        </div>
      ),
    },
  ];
  return (
    <div className="space-y-3">
      <QueryView query={q}>{(d) => <DataTable caption="Evidence records" columns={cols} rows={d.items} rowKey={(e) => e.evidence_id} empty="No evidence recorded. A claim cannot be published without it." />}</QueryView>
      {[confirm, lifecycle].map((m, i) => (m.error ? <RegError key={i} error={m.error} /> : null))}
    </div>
  );
}

function Reports() {
  const q = useCellQuery([...K, "reports"], (api) => api.trustCenter.reports(true));
  const triage = useCellMutation((api, v: { id: string; state: string }) => api.trustCenter.triage(v.id, { state: v.state }), { invalidate: [K as never], success: "Recorded" });
  const NEXT: Record<string, string[]> = { RECEIVED: ["ACKNOWLEDGED", "OUT_OF_SCOPE"], ACKNOWLEDGED: ["TRIAGED", "OUT_OF_SCOPE"], TRIAGED: ["FIXING", "CLOSED"], FIXING: ["FIXED"], FIXED: ["DISCLOSED", "CLOSED"] };
  return (
    <div className="space-y-3">
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Open vulnerability reports"
            columns={[
              { key: "ref", header: "Reference", render: (r) => r.reference },
              { key: "sev", header: "Severity", render: (r) => r.severity },
              { key: "state", header: "State", render: (r) => <Badge tone={r.state === "RECEIVED" ? "warn" : "info"}>{r.state.toLowerCase().replaceAll("_", " ")}</Badge> },
              { key: "due", header: "First response by", render: (r) => new Date(r.ack_due).toLocaleString() },
              { key: "act", header: "", render: (r) => <div className="flex gap-1">{(NEXT[r.state] ?? []).map((s) => <Button key={s} variant="secondary" onPress={() => triage.mutate({ id: r.report_id, state: s })}>{s.toLowerCase().replaceAll("_", " ")}</Button>)}</div> },
            ]}
            rows={d.items}
            rowKey={(r) => r.report_id}
            empty="No open reports."
          />
        )}
      </QueryView>
      {triage.error ? <RegError error={triage.error} /> : null}
    </div>
  );
}

function Withdrawals() {
  const q = useCellQuery([...K, "withdrawals"], (api) => api.trustCenter.withdrawals(false));
  const [note, setNote] = useState("");
  const review = useCellMutation((api, id: string) => api.trustCenter.reviewWithdrawal(id, note), { invalidate: [K as never], success: "Review recorded" });
  return (
    <div className="space-y-3">
      <TextField label="Review note" value={note} onChange={setNote} description="What you found. The review cannot be done by the person who withdrew the claim." />
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Emergency withdrawals"
            columns={[
              { key: "at", header: "Withdrawn", render: (w) => new Date(w.at).toLocaleString() },
              { key: "why", header: "Reason", render: (w) => w.reason },
              { key: "due", header: "Review due", render: (w) => new Date(w.review_due).toLocaleString() },
              { key: "st", header: "Review", render: (w) => (w.reviewed_at ? <Badge tone="ok">done</Badge> : <Button variant="secondary" isDisabled={note.trim().length < 3} onPress={() => review.mutate(w.withdrawal_id)}>Record review</Button>) },
            ]}
            rows={d.items}
            rowKey={(w) => w.withdrawal_id}
            empty="No withdrawals."
          />
        )}
      </QueryView>
      {review.error ? <RegError error={review.error} /> : null}
    </div>
  );
}

function Availability() {
  const q = useCellQuery([...K, "snapshots"], (api) => api.trustCenter.snapshots());
  const validate = useCellMutation((api, id: string) => api.trustCenter.validateSnapshot(id), { invalidate: [K as never], success: "Validated: now shown publicly" });
  return (
    <div className="space-y-3">
      <Banner tone="info" title="A figure is public only after a second person validates it">Record a figure from raw counts; a different person validates it. An empty window is never shown as 100%.</Banner>
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Availability figures"
            columns={[
              { key: "s", header: "Service", render: (r) => `${r.service} (${r.region})` },
              { key: "w", header: "Window", render: (r) => r.window },
              { key: "f", header: "Figure", render: (r) => figure(r.percent, r.state) },
              { key: "src", header: "Source", render: (r) => r.source_ref },
              { key: "v", header: "", render: (r) => (r.validated ? <Badge tone="ok">validated</Badge> : <Button variant="secondary" onPress={() => validate.mutate(r.snapshot_id)}>Validate</Button>) },
            ]}
            rows={d.items}
            rowKey={(r) => r.snapshot_id}
            empty="No figures recorded. The public page says: not yet reported."
          />
        )}
      </QueryView>
      {validate.error ? <RegError error={validate.error} /> : null}
    </div>
  );
}

function Disclosures() {
  const q = useCellQuery([...K, "disclosures"], (api) => api.trustCenter.disclosures());
  const approve = useCellMutation((api, id: string) => api.trustCenter.approveDisclosure(id), { invalidate: [K as never], success: "Approved: now public for that market" });
  const [region, setRegion] = useState("");
  const check = useCellQuery([...K, "region", region], (api) => api.trustCenter.regionCheck(region), { enabled: /^[A-Z]{2}$/.test(region) });
  return (
    <div className="space-y-3">
      <Card title="May we say data stays in a country?" description="Blocked while any approved subprocessor holding restricted data, or any transfer, sits outside it.">
        <TextField label="Country code" value={region} onChange={(v) => setRegion(v.toUpperCase())} />
        {check.data ? check.data.may_claim_data_stays ? <Banner tone="ok" title="No approved record contradicts it" /> : <Banner tone="danger" title="Not yet"><ul className="list-disc pl-5">{check.data.blockers.map((b) => <li key={b}>{b}</li>)}</ul></Banner> : null}
      </Card>
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Privacy disclosures"
            columns={[
              { key: "k", header: "Kind", render: (r) => r.kind },
              { key: "s", header: "Subject", render: (r) => `${r.subject} (${r.market}) revision ${r.revision}` },
              { key: "st", header: "State", render: (r) => <Badge tone={r.status === "ACTIVE" ? "ok" : "neutral"}>{r.status.toLowerCase()}</Badge> },
              { key: "a", header: "", render: (r) => (r.status === "DRAFT" ? <Button variant="secondary" onPress={() => approve.mutate(r.disclosure_id)}>Approve</Button> : null) },
            ]}
            rows={d.items}
            rowKey={(r) => r.disclosure_id}
            empty="No disclosures. The public page says each kind is not yet published."
          />
        )}
      </QueryView>
      {approve.error ? <RegError error={approve.error} /> : null}
    </div>
  );
}

function Room() {
  const q = useCellQuery([...K, "room"], (api) => api.trustCenter.roomRequests(true));
  const [reason, setReason] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const decide = useCellMutation((api, v: { id: string; approve: boolean }) => api.trustCenter.decideRoom(v.id, { approve: v.approve, reason }), {
    invalidate: [K as never], success: "Decision recorded", onSuccess: (r) => setToken(r.access_token ?? null),
  });
  return (
    <div className="space-y-3">
      {token ? <Banner tone="warn" title="Give this token to the requester now. It is shown once and works only for them."><code className="break-all">{token}</code></Banner> : null}
      <TextField label="Reason (required to refuse)" value={reason} onChange={setReason} />
      <QueryView query={q}>
        {(d) => (
          <DataTable
            caption="Requests for restricted documents"
            columns={[
              { key: "o", header: "Company", render: (r) => r.organization },
              { key: "p", header: "Purpose", render: (r) => r.purpose },
              { key: "d", header: "Asked", render: (r) => new Date(r.created_at).toLocaleString() },
              { key: "a", header: "", render: (r) => <div className="flex gap-1"><Button variant="secondary" onPress={() => decide.mutate({ id: r.request_id, approve: true })}>Approve</Button><Button variant="secondary" isDisabled={reason.trim().length < 3} onPress={() => decide.mutate({ id: r.request_id, approve: false })}>Refuse</Button></div> },
            ]}
            rows={d.items}
            rowKey={(r) => r.request_id}
            empty="No open requests."
          />
        )}
      </QueryView>
      {decide.error ? <RegError error={decide.error} /> : null}
    </div>
  );
}

export function TrustOperationsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Trust Center operations"
        description="Watch that what is public is still true, and work the queues that feed it: evidence, vulnerability reports, withdrawals, measured availability, privacy disclosures and restricted-document requests."
        breadcrumbs={[{ label: "Trust Center" }, { label: "Operations" }]}
      />
      <Tabs
        label="Trust Center operations"
        tabs={[
          { id: "monitoring", label: "Monitoring", content: <Monitoring /> },
          { id: "evidence", label: "Evidence", content: <Evidence /> },
          { id: "reports", label: "Vulnerability reports", content: <Reports /> },
          { id: "withdrawals", label: "Withdrawals", content: <Withdrawals /> },
          { id: "availability", label: "Availability", content: <Availability /> },
          { id: "privacy", label: "Privacy", content: <Disclosures /> },
          { id: "room", label: "Document requests", content: <Room /> },
        ]}
      />
    </div>
  );
}
